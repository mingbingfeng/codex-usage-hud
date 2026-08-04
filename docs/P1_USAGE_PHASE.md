# P1 Usage Phase Evidence

Completed: 2026-07-30 (Asia/Shanghai)

## Ownership

- `runtime_orchestration.py`: 11965 -> 10464 lines across P1.
- `usage_contributions.py`: provider-neutral per-file DTOs, canonical paths,
  session/archive enumeration, stat tokens, tail/parser-version state.
- `usage_cache.py`: 692-line query/invalidation facade with provider filtering.
- `usage_insights.py`: 746-line grouping/projection and worker lifecycle owner.
- `session_cleanup_runtime.py`: deleted-usage transactions and cleanup worker.
- `runtime_usage.py`: unchanged pure usage arithmetic and current-task projection.

The CLI compatibility export remains identity-preserving:
`codex_usage_hud.cli.UsageSummaryCache is codex_usage_hud.usage_cache.UsageSummaryCache`.

## Behavioral Proof

- Append reuses the same `JsonlTailState`; parser-version change resets it.
- Incremental append totals equal a fresh full rebuild.
- Unchanged current-file refresh short-circuits on file/window identity.
- Provider filtering remains above raw contribution state.
- Deleted usage prepare/commit/discard and verified-delete refresh are covered at
  the cleanup runtime owner.
- Insights worker ready/failure/close and cleanup worker execute/close paths are
  covered at their owners.

## Gates

- P1 owner/architecture/session-cleanup/latency unit gate: 56 passed.
- Stable non-UI gate: only the four exact P2 overlay baseline failures occurred.
- Qt gate: 2 passed; 38 removed legacy product tests skipped.
- Compileall, changed-scope Ruff, facade inventory, and `git diff --check`: passed.
- Facade patch inventory: 80 classified paths / 443 references; no usage test uses
  a `codex_usage_hud.cli.*` usage patch.

## Latency Artifact

Artifact: `%TEMP%\codex-hud-p1-latency-final2.md`, 15 iterations / 3 warmups.

| Operation | P90 ms | Budget ms |
|---|---:|---:|
| current session full parse | 39.786 | 50 |
| renderer payload build | 5.775 | 25 |
| usage full scan | 63.148 | 250 |
| unchanged current-file refresh | 0.537 | 25 |
| degraded watcher signature | 24.213 | 250 |
| append incremental parse and payload | 37.854 | 250 |

All latency budgets pass. P1 exits; P2 must remove the four overlay baseline entries.
