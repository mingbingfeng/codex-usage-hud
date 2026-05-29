# Changelog

All notable changes to this project will be documented in this file.

This project adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this repository follows Semantic Versioning for release tags.

## [Unreleased]

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
