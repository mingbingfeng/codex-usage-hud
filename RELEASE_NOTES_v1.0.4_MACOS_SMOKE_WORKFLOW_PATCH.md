# codex-usage-hud v1.0.4 - macOS Smoke Workflow Patch

> Release body for GitHub Releases.

## TL;DR

`v1.0.4` supersedes `v1.0.3` with the same renderer-only runtime release scope
and restores the macOS debug-launch regression test referenced by the GitHub
Actions `macOS Smoke` workflow.

## Fixed

- Restored coverage for `launch_codex_app(debugger=True)` on macOS so the smoke
  workflow validates the expected `open -a Codex --args --remote-debugging-port`
  command line.

## Included From v1.0.3

- Main HUD runtime is renderer-only; legacy Qt/Tk main HUD fallback entry points
  are disabled instead of being used after renderer failures.
- Deprecated display modes normalize to renderer, and renderer CDP failures now
  return diagnostics rather than silently switching surfaces.
- The default CLI import path no longer eagerly imports Tk, Qt HUD, PySide6, or
  the desktop work-overlay helper.
- macOS recursive session tree watching falls back to polling when kqueue would
  miss nested `sessions/YYYY/MM/*.jsonl` updates.

## Verification

```powershell
python -m pytest
python -m pytest -m ui
python -m compileall -q src tools tests
python tools/pre_release_check.py
python tools/build_installer.py
```

## Notes

- Windows installer asset:
  `codex-usage-hud-v1.0.4-windows-x64-setup.exe`.
- This repository still has no supported macOS installer path. macOS support is
  source / pip plus the GitHub Actions `macOS Smoke` compatibility gate.
