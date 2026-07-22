# Implement: Safe Cleanup Scan Progressive Feedback

## Checklist

1. [x] Extend `SafeCleanupManager.scan` with phased progress + partial snapshots via publisher callback
2. [x] Wire `_SafeCleanupWorker` to set publisher during scan/preview pipeline
3. [x] Session cleanup: mark scanning phases (minimal) before full scan returns
4. [x] Renderer: scan boot shell, strip, progressive rows, rescan veil, session busy UI
5. [x] Preserve last stable cleanup payload client-side for rescan
6. [x] Tests: safe_cleanup progress events; renderer HTML markers for scanning
7. [x] Update `safe-cleanup-contracts.md` with operation progress fields
8. [x] ruff + targeted pytest

## Validation

```bash
python -m ruff check src/codex_usage_hud/core/safe_cleanup.py src/codex_usage_hud/core/session_cleanup.py src/codex_usage_hud/cli.py src/codex_usage_hud/ui/renderer_hud.py
python -m pytest tests/test_safe_cleanup.py tests/test_renderer_hud.py tests/test_session_cleanup.py -q --tb=short
```

## Rollback points

- After Python-only: UI still shows button busy if UI not landed
- After UI: disable progressive branch with state check if needed

## Review gates

- No absolute paths in progressive payload groups
- Confirm button never enabled during scanning
- Final preview/execute path unchanged
