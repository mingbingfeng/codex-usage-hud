# codex-usage-hud v1.0.3 - Renderer-Only Cross-Platform Patch

> Release body for GitHub Releases.

## TL;DR

`v1.0.3` makes renderer mode the only supported main HUD runtime, keeps the
optional PySide6 desktop work-bubble helper, and tightens macOS/Windows
compatibility around renderer injection, session tracking, file watching, and
package import boundaries.

## Changed

- Main HUD runtime is now renderer-only. Legacy Qt/Tk main HUD fallback entry
  points are disabled instead of being used after renderer failures.
- Deprecated display modes normalize to renderer, and renderer CDP failures now
  return diagnostics rather than silently switching surfaces.
- The default CLI import path no longer eagerly imports Tk, Qt HUD, PySide6, or
  the desktop work-overlay helper.
- The default Windows packaged build path continues to exclude PySide6; desktop
  work bubbles remain available through the optional `desktop-overlay` extra.

## Fixed

- macOS CDP launch/probing paths now support Codex debug launch and active
  session discovery.
- macOS recursive session tree watching falls back to polling when kqueue would
  miss nested `sessions/YYYY/MM/*.jsonl` updates.
- Windows-only platform imports no longer require Windows ctypes symbols on
  macOS and other non-Windows platforms.

## Verification

```powershell
python -m pytest
python -m compileall -q src tools tests
python tools/pre_release_check.py
python tools/build_installer.py
```

## Notes

- Windows installer asset:
  `codex-usage-hud-v1.0.3-windows-x64-setup.exe`.
- This repository still has no supported macOS installer path. macOS support is
  source / pip plus the GitHub Actions `macOS Smoke` compatibility gate.
- Runtime behavior remains local-first: no telemetry, no log upload, and no
  prompt/response upload.
