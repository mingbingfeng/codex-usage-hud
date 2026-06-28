# Changelog

All notable changes to this project will be documented in this file.

This project adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this repository follows Semantic Versioning for release tags.

## [Unreleased]

## [1.0.4] - 2026-06-28

macOS Smoke Workflow Patch.

### Fixed

- Restored the macOS Codex debug-launch argument regression test referenced by
  the `macOS Smoke` workflow.

## [1.0.3] - 2026-06-28

Renderer-Only Cross-Platform Patch.

### Changed

- Switched the runtime surface to renderer-only: legacy `auto`, `qt`, `tk`,
  `pyside6`, and `tkinter` display modes now normalize to `renderer`, and
  CDP failures return diagnostics instead of falling back to standalone HUDs.
- Stopped importing Tk, Qt HUD, PySide6, or the desktop work-overlay helper from
  the default CLI import graph.
- Added macOS CDP active-session probing and Codex debug launch support.
- Removed PySide6 and Tk/PySide hidden imports from the default package and
  Windows PyInstaller build path.
- Restored the PySide6 desktop work-bubble overlay as an optional helper driven
  from renderer sessions when `work_overlay_max_items` is enabled.
- Added a GitHub Actions `macOS Smoke` workflow to verify desktop-overlay
  installability, lazy CLI imports, source compilation, and task-related tests
  on `macos-latest`.

### Fixed

- Hardened Windows-only platform modules so cross-platform imports no longer
  require `ctypes.WINFUNCTYPE` at import time on macOS and other non-Windows
  environments.

## [1.0.2] - 2026-06-18

Completed Bubble Transition Patch.

### Fixed

- Reworked the work-overlay completion animation so cards shrink in place,
  move along the right-side vertical track, and restore back through the same
  path when a finished task becomes active again.
- Restored the information-rich completed bubble badge after the transition,
  including the title, runtime, token/cost/cache metrics, workdir label, and
  click-through affordances.
- Tightened startup filtering so historical completed tasks do not appear as
  fresh completed bubbles before the current runtime has actually seen them run.
- Moved work-overlay session-switch command draining off the UI refresh path so
  click-to-activate stays responsive while the HUD keeps repainting.
- Normalized the CDP websocket handshake authority/origin fields for IPv6 and
  loopback hosts so renderer probing and session switching stay compatible with
  more local debugger endpoints.

## [1.0.1] - 2026-06-11

Archived Sessions Summary Patch.

### Fixed

- Taught usage summary scans to include the sibling `archived_sessions`
  directory when the live `sessions` tree is summarized, so day/week totals
  stay correct after archived logs move out of the active folder.
- Extended active-session discovery and path resolution so archived session
  trees are considered alongside the live `sessions` tree, and unmatched
  tracker states can still fall back to the newest viable session.
- Kept the realtime watcher polling after event gaps, which helps the HUD
  recover when title events pause before the next backstop poll.
- Removed the redundant close button from the Tk expanded header so the top
  controls match the current layout and the close action stays in the window
  controls.

## [1.0.0] - 2026-06-09

Windows Installer Edition, the first supported installer-based release line.

### Added

- Added GitHub Release update helpers for checking the latest release, selecting
  the Windows setup asset, downloading it, and launching the installer.
- Added CLI update surfaces: `--version`, `--check-update`, and `--update`.
- Added a renderer HUD "版本更新" settings tab with current version display,
  update checking, and installer launch commands.
- Added a Tk settings "版本更新" tab so the fallback UI exposes the same version
  and update flow.
- Added an Inno Setup 6 installer script and `tools/build_installer.py` to
  produce `codex-usage-hud-vX.Y.Z-windows-x64-setup.exe`.
- Added README screenshots for the v1.0.0 HUD and update settings surfaces.
- Added `README_EN.md`; `README.md` now defaults to Chinese with an English
  companion file.

### Changed

- Promoted the package version to `1.0.0` and metadata to stable release status.
- Updated the PyInstaller build helper to collect bundled QR-code image assets
  into the single-file executable.
- Reworked the GitHub homepage README around the reference module structure:
  quick start, sponsors, support, features, pain points, auto update and
  installer, data locations, FAQ, development, and notes.
- Marked `v0.1.0`, `v0.2.0`, and `v0.3.0` as historical alpha / preview tags
  rather than recommended install entry points.

### Fixed

- Made the packaged Windows flow stop an existing HUD before replacing the
  installed executable, reducing update failures from locked files.

## [0.3.0] - 2026-06-05

Renderer Timeline Edition, focused on keeping the HUD in the Codex renderer
surface while preserving the Tk fallback path.

### Added

- Added a renderer-injected HUD driven through local Chrome DevTools Protocol,
  with top session/budget status and bottom request timeline panels rendered
  inside the Codex UI.
- Added CDP-based active conversation probing so the HUD can resolve the active
  Codex thread by session id or title before falling back to native title
  tracking.
- Added CLI switches to prefer the renderer HUD by default and force the legacy
  Tk HUD with `--tk-hud` / `--no-renderer-hud`.
- Added regression coverage for CDP target selection, renderer payloads, and
  renderer HUD client installation/update behavior.

### Changed

- Updated the live HUD command to prefer renderer injection when Codex exposes a
  local CDP target, with automatic fallback to Tk when renderer injection is not
  available.
- Improved top HUD anchoring so long Codex conversation titles, the three-dot
  conversation menu, and right-side header actions are treated as hard
  avoidance zones.
- Improved bottom request HUD anchoring so it follows the composer footer row
  and remains stable when attachments or image previews expand the composer.
- Refined request timeline formatting so cache hit rate follows input tokens,
  cached tokens are second-last, and total tokens are last.
- Kept Tk anchoring and geometry behavior aligned with the renderer follow
  model while preserving its fallback role.

### Fixed

- Preserved the application title-bar drag region while allowing HUD panels to
  be moved and resized inside the renderer.
- Removed duplicate reset controls and redundant HUD glyphs that cluttered the
  renderer panels.
- Fixed the bottom expanded timeline header so "round flow / newest first" no
  longer overlaps the request list.

## [0.2.0] - 2026-05-29

Smart Daemon Edition and repository polish for the current release line.

### Added

- Added Windows daemon mode for low-noise session tracking and auto-attach.
- Added optional startup persistence paths in `install.bat`.
- Added a single-file EXE build helper for local packaging.
- Added a fuller README homepage structure with install, usage, release, and maintenance sections.
- Added a top-level contributing guide so new contributors have a single entry point.

### Changed

- Synced the package version with the current `v0.2.0` tag.
- Updated project metadata links to the current repository.
- Aligned the release history entry points across README, changelog, and release note drafts.

## [0.1.0] - 2026-05-28

First public alpha release. The project has moved from a single-file prototype
into a modular, installable Python package with local-first data handling,
cross-platform discovery, a live SSE state machine, and a polished Tk HUD.

### Added

- Rebuilt the original single-file proof of concept into a standard `src/`
  package layout with separate `core`, `platforms`, and `ui` layers, plus CLI
  and module entry points.
- Added multi-platform Codex data discovery for Windows, macOS, and Linux, with
  fallback probing of the standard local Codex data directories.
- Added resilient JSONL parsing that tolerates partial trailing lines and
  reconstructs session state from local logs without crashing on incomplete
  writes.
- Added a read-only SQLite SSE state machine for live request tracking, using
  `mode=ro` access so local Codex databases are not locked by the HUD.
- Added accurate token accounting, including cached input tokens, reasoning
  tokens, and model-aware pricing with cached-token discount handling.
- Added rolling day/week usage summaries and budget threshold warnings directly
  from local session history.
- Added active-session mapping from conversation title, session index, and
  thread metadata so the HUD can follow the current conversation more reliably.
- Added a Tk HUD with dock/attach behavior, expanded drawer views, draggable
  and resizable windows, and long-text auto-scroll / numeric tween animations.
- Added CLI modes for one-shot snapshots and live HUD operation, plus compact
  output for terminal use.
- Added a zero-dependency runtime built entirely on the Python standard
  library.
- Added regression coverage that locks the parser, calculator, platform
  discovery, active-session resolver, and UI behavior in 39 unit tests.

### Changed

- Standardized the project into an installable package with `pyproject.toml`, a
  `codex-hud` console script, and `python -m codex_usage_hud` support.
- Separated platform-specific logic into dedicated adapters instead of mixing
  OS branching into the UI and parser layers.
- Refined session selection so pinned file, pinned session id, active-session
  follow, and activity-based fallback all resolve through one path.
- Improved status output so the CLI and HUD present session tokens, current task
  tokens, today/week totals, and budget state consistently.
- Persisted HUD geometry and window state so docked and free-floating layouts
  survive restarts.
- Tightened the overall engineering surface to favor reusable primitives,
  explicit boundaries, and easier future maintenance.

### Security

- Added [docs/PRIVACY.md](docs/PRIVACY.md) as a hard local-first privacy
  boundary.
- Added a GitHub issue template that warns contributors not to upload raw
  JSONL logs, raw SQLite databases, or unredacted prompts/responses.
- Kept local database access read-only and avoided telemetry, upload, or
  network paths by design.
