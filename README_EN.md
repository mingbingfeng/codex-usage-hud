# codex-usage-hud

[中文](README.md) | English

> **Live token usage, cache hit rate, and real cost inside Codex App — fully local, no data uploaded.**
> Stop API relay "background spending" and blind-waiting on long tasks.

![Live HUD Demo](docs/images/demo-hud-animation.gif)
![Completion Badge Demo](docs/images/demo-completion-badges.gif)

[![Release](https://img.shields.io/github/v/release/mingbingfeng/codex-usage-hud?label=release)](https://github.com/mingbingfeng/codex-usage-hud/releases)
[![License](https://img.shields.io/github/license/mingbingfeng/codex-usage-hud)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-supported-0078D4)](https://github.com/mingbingfeng/codex-usage-hud/releases)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)](pyproject.toml)
[![GitHub Stars](https://img.shields.io/github/stars/mingbingfeng/codex-usage-hud?style=social)](https://github.com/mingbingfeng/codex-usage-hud/stargazers)

`codex-usage-hud` injects a usage panel directly into the Codex UI (renderer injection, not a separate floating window): session tokens, cache hit rate, live USD estimate, daily/weekly budgets, and waiting status all on one screen. All data is read from your local Codex JSONL / SQLite logs only. **No telemetry, no prompt/response upload, no cloud account required.**

### Why you need it
- 💸 **Cost transparency, stop hidden spending** — API relays have opaque billing, and background requests can quietly run for hours before you notice. The HUD keeps your session/daily/weekly cost visible right next to Codex so you catch anomalies immediately.
- ⏳ **No more blind waiting on long tasks** — see in real-time if a request is running, which tool is slowest, and how long you've been waiting. Know if it's still working or if you should intervene.
- 🔒 **Privacy first** — fully local, zero telemetry, open-source and auditable.

## Quick Start

Download the latest Windows installer from [GitHub Releases](https://github.com/mingbingfeng/codex-usage-hud/releases):

- Windows: `codex-usage-hud-v*-windows-x64-setup.exe`

After installation, Start Menu shortcuts are available:

- `Codex Usage HUD`: daemon entry; when Codex App is not running, it prompts to start Codex App in debug/CDP mode and inject the renderer HUD. Login startup entries keep waiting silently.
- `Stop Codex Usage HUD`: stops the running HUD.
- `Check for Updates`: checks GitHub Releases for a newer installer.

CLI commands are also available:

```powershell
codex-hud --once
codex-hud --daemon
codex-hud --stop
codex-hud --check-update
codex-hud --update
```

## Sponsors

[Want to be listed below?](https://github.com/mingbingfeng/codex-usage-hud/issues)

| Sponsor | Description |
| --- | --- |

## Community and Support

For questions, suggestions, or discussions, head to [GitHub Discussions](https://github.com/mingbingfeng/codex-usage-hud/discussions). I actively monitor and respond there.

If this HUD saves you time while checking token usage and cost, you can support ongoing maintenance with the bundled Alipay or WeChat QR codes.

<p>
  <img src="src/codex_usage_hud/assets/sponsor_alipay.jpg" alt="Alipay QR code" width="210">
  <img src="src/codex_usage_hud/assets/sponsor_wechat.jpg" alt="WeChat reward QR code" width="210">
</p>

## Main Features

- Renderer-only HUD: when Codex exposes a local CDP target, the HUD renders inside Codex App; when CDP is unavailable, the app reports diagnostics instead of falling back to Qt/Tk standalone windows.
- PySide6 desktop work bubbles: source / pip installs can enable square running bubbles and completed circular bubbles with `codex-usage-hud[desktop-overlay]`; the main HUD remains renderer-only.
- Codex theme sync: Renderer mode follows the live Codex App theme; without an available CDP target, diagnostics point you to restart Codex App with the debug port enabled.
- Real-time tokens and cost: input, cached input, output, reasoning, total, cache hit rate, and live USD estimate.
- Day/week budgets: custom limits, reset time, weekly reset day, thresholds, and manual weekly adjustment.
- Work status visibility: current activity, longest wait, slowest tool, and request timeline.
- Local settings: stored in the current user's `hud_settings.json`.
- Auto update: both settings UI and CLI can check GitHub Releases and launch the Windows installer.
- Windows installer: v1.0.0 ships an Inno Setup installer with Start Menu and optional desktop shortcuts.

## Pain Points and Fixes

Relay token usage and billing can be opaque, and background spending is hard to notice. `codex-usage-hud` keeps session, daily, and weekly cost next to Codex, with cache hit rate and estimated cost visible in real time.

![Real-time usage HUD](docs/images/codex-usage-hud-v1-dashboard.png)

Long Codex tasks can feel like blind waiting. The HUD shows whether the current request is still running, when it last refreshed, which tool was slowest, and where time is being spent.

Budget windows differ between users and providers. You can customize daily/weekly limits, reset dates, reset times, thresholds, and manual weekly adjustments to mirror your own relay or team budget.

![Settings panel](docs/images/codex-usage-hud-v1-settings.png)

Older script-based startup and shutdown flows were awkward. v1.0.0 adds a Windows installer, Start Menu daemon shortcut, stop shortcut, and update shortcut so daily use no longer depends on remembering Python commands.

![Version and update settings](docs/images/codex-usage-hud-v1-update.png)

Raw usage data is split across JSONL files, SQLite databases, and session indexes. The HUD merges those local sources into one snapshot and keeps `codex-hud --once` for quick checks and automation.

## Auto Update and Installer

Starting with v1.0.0, `codex-usage-hud` is released through GitHub Release Windows installers:

- Installer name: `codex-usage-hud-vX.Y.Z-windows-x64-setup.exe`
- Build command: `python tools/build_installer.py`
- Installer technology: Inno Setup 6
- Default install location: `%LOCALAPPDATA%\Programs\codex-usage-hud`

The settings UI has a Version Update tab that can check the latest release and launch the installer. CLI equivalents:

```powershell
codex-hud --check-update
codex-hud --update
```

The installer runs `codex-hud --stop` before replacing files so the previous HUD process does not keep the executable locked.

Current release strategy:

- Official releases are still centered on the Windows installer, and the default packaged build does not bundle PySide6 desktop bubble dependencies.
- If desktop work bubbles are needed today, the recommended path is a source / pip environment with `codex-usage-hud[desktop-overlay]`.
- macOS does not currently ship an installer; it stays on the source / pip path plus `macOS Smoke` code-level validation. See [docs/DESKTOP_OVERLAY_RELEASE_STRATEGY.md](docs/DESKTOP_OVERLAY_RELEASE_STRATEGY.md).

## Data Locations

- Codex session logs: `~/.codex/sessions/`
- Codex SSE / state databases: `~/.codex/logs_2.sqlite`, `~/.codex/state_5.sqlite`
- HUD settings: `%LOCALAPPDATA%\codex-usage-hud\hud_settings.json`
- HUD daemon log: `%LOCALAPPDATA%\codex-usage-hud\daemon.log`
- Default install directory: `%LOCALAPPDATA%\Programs\codex-usage-hud`

## Codex Theme Sync

The HUD can now follow the active Codex App theme, including light/dark
variants and `Copy theme` share strings. For implementation details, theme
export steps, and current limitations, see
[docs/CODEX_APP_THEME_SYNC.md](docs/CODEX_APP_THEME_SYNC.md).

## FAQ

### Are the old v0.x tags still recommended?

No. `v0.1.0`, `v0.2.0`, and `v0.3.0` remain as historical alpha / preview tags. `v1.0.0` is the first supported Windows installer release.

### The HUD does not appear inside Codex

Start it from `Codex Usage HUD` or `codex-hud --daemon`. The HUD requires Codex App to expose a local CDP/debug target. When Codex App is not running, the HUD prompts to start it with debugging/CDP enabled and keeps waiting/retrying renderer injection. Windows may show one elevation prompt if direct launch is blocked. Login startup uses `--no-startup-prompt`, so it does not show the mode prompt.

### Desktop work bubbles do not appear

The `work_overlay_max_items` setting controls the number of PySide6 desktop-level session bubbles; set it to `0` to disable desktop bubbles. If `codex-usage-hud[desktop-overlay]` is not installed, the renderer HUD still works and records a single `work_overlay_unavailable` diagnostic.

### Does it upload prompts or logs?

No. The project reads local logs and databases only. It does not send telemetry, upload prompts/responses, or require a cloud account. Read [docs/PRIVACY.md](docs/PRIVACY.md) before sharing issue screenshots or logs.

## Development

```powershell
python -m pip install -e ".[desktop-overlay]"  # optional: PySide6 desktop work bubbles
python -m compileall -q src tools tests
python -m pytest
python -m pytest -m ui        # optional: legacy Tk/Qt module window regressions
python tools/build_exe.py
python tools/build_installer.py
```

macOS without a local device:

- The GitHub Actions `macOS Smoke` workflow runs on `macos-latest` and covers `codex-usage-hud[desktop-overlay]` installation, lazy imports, `compileall`, and task-related pytest coverage.
- This workflow is code-level smoke coverage only, not a substitute for real desktop interaction validation; the current round does not use a remote Mac, so macOS desktop bubbles are still pending real-device confirmation. See [docs/MACOS_VALIDATION.md](docs/MACOS_VALIDATION.md) for the parked manual checklist.

Project structure:

```text
src/codex_usage_hud/
  cli.py                 CLI, daemon, and update command entry
  daemon.py              Windows Codex process listener
  ui/renderer_hud.py     Codex renderer-injected HUD
  ui/work_overlay_qt.py  Optional PySide6 desktop work-bubble helper
  ui/qt_hud.py           Legacy Qt standalone module, not loaded by default
  ui/tk_hud.py           Legacy Tk standalone module, not loaded by default
  updater.py             GitHub Release update check and installer launch
tools/
  build_exe.py           PyInstaller single-file exe build
  build_installer.py     Inno Setup installer build
  installer/             Inno Setup script
tests/                   Parser, UI, daemon, packaging, and updater tests
```

## Notes

`codex-usage-hud` is an external local monitoring tool. It does not modify Codex App installation files. Codex App or log format changes may require parser or renderer-injection updates.
