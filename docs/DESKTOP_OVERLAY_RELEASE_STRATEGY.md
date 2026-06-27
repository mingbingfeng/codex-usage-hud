# Desktop Overlay Release Strategy

## Current Decision

This project currently treats the PySide6 desktop work-bubble overlay as an
optional source / pip capability, not a default packaged runtime dependency.

### Windows

- Official release artifact remains the Windows installer:
  `codex-usage-hud-vX.Y.Z-windows-x64-setup.exe`
- The default Windows packaged build does **not** bundle PySide6.
- Users who need desktop work bubbles should use a source / pip environment and
  install `codex-usage-hud[desktop-overlay]`.

### macOS

- There is currently **no** official macOS installer or auto-update path.
- macOS support is limited to source / pip environments.
- Code-level compatibility is gated by GitHub Actions `macOS Smoke`.
- Real desktop interaction is still pending future real-device verification.

## Validated State

As of 2026-06-27:

- GitHub Actions run `28283179999` passed on `macos-latest`.
- The run verified:
  - `python -m pip install -e ".[desktop-overlay]"`
  - lazy `import codex_usage_hud.cli`
  - `python -m compileall -q src tests tools`
  - task-related pytest coverage

This is sufficient to claim macOS code-level support for:

- installability
- importability
- smoke-test execution

It is **not** sufficient to claim:

- overlay z-order correctness
- click-to-activate behavior on a real macOS desktop
- helper cleanup under real interactive use

## Release Matrix

| Surface | Current status | PySide6 bundled by default | Notes |
|---|---|---:|---|
| Windows installer | supported | no | official GitHub Release path |
| Windows source / pip | supported | optional | install `.[desktop-overlay]` when needed |
| macOS source / pip | limited | optional | gated by CI smoke, not by real-device validation |
| macOS installer | not supported | n/a | intentionally deferred |

## Required Gates

### For ordinary releases

- `python -m pytest`
- `python -m compileall -q src tools tests`
- `python tools/pre_release_check.py`

### For releases that touch renderer / overlay / platform import boundaries

- latest `macOS Smoke` workflow is green
- Windows manual overlay verification remains green if desktop bubbles changed
- docs continue to state that macOS desktop interaction is not yet real-device validated

## Deferred Decisions

These are intentionally not decided in the current round:

1. Bundle PySide6 into the default Windows installer.
2. Publish a separate Windows overlay-enabled installer.
3. Ship a macOS app bundle, installer, signing, or auto-update flow.
4. Treat macOS desktop bubbles as officially validated without real-device testing.

## Conditions To Revisit

Revisit this strategy only if at least one of these becomes true:

1. Users explicitly need desktop work bubbles from the packaged Windows installer.
2. The project starts producing official macOS artifacts.
3. A real Mac verification path becomes available and repeatable.
4. PySide6 bundle size / startup cost is measured and considered acceptable.
