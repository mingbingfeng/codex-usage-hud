# codex-usage-hud v0.3.0 · Renderer Timeline Edition

> Release body for GitHub Releases.

## TL;DR

`v0.3.0` moves the live HUD into the Codex renderer when a local Chrome
DevTools Protocol target is available, while keeping the Tk HUD as the fallback
surface. The renderer HUD follows the Codex conversation title bar and composer
footer inside the same rendering plane, reducing the jitter that came from
separate native overlay windows.

## Headliners

### Renderer-injected HUD

- Adds a CDP-driven renderer HUD with a top session/budget panel and a bottom
  request timeline panel.
- Keeps the HUD inside the Codex renderer surface instead of tracking a separate
  native overlay window.
- Preserves the application title-bar drag region while allowing the HUD panels
  themselves to move, resize, and expand.

### Better Codex App following

- Resolves the active Codex conversation through CDP by session id and title
  before falling back to native title tracking.
- Anchors the top HUD around the real conversation title, three-dot menu, and
  right-side header buttons so long titles are not covered.
- Anchors the bottom HUD to the composer footer row so image and attachment
  previews do not push it upward.

### Cleaner request timeline

- Reorders bottom request stats so input is followed by cache hit rate, then
  output/reasoning, cached tokens, and total tokens.
- Removes redundant reset buttons and decorative glyphs from the renderer HUD.
- Fixes the expanded request timeline header so "round flow / newest first" no
  longer overlaps the request rows.

## Verification

```powershell
python -m compileall -q src tests
python -m unittest discover -s tests
```

Current validation: 112 unit tests passing.

## Notes

- The renderer HUD is preferred by default. Use `--tk-hud` or
  `--no-renderer-hud` to force the legacy Tk HUD.
- The implementation remains local-first and dependency-free at runtime; CDP is
  used only against the local Codex renderer debug target when available.
