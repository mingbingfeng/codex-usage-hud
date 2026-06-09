# codex-usage-hud

[中文](README.md) | English

[![Release](https://img.shields.io/github/v/release/mingbingfeng/codex-usage-hud?label=release)](https://github.com/mingbingfeng/codex-usage-hud/releases)
[![License](https://img.shields.io/github/license/mingbingfeng/codex-usage-hud)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-supported-0078D4)](https://github.com/mingbingfeng/codex-usage-hud/releases)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)](pyproject.toml)

`codex-usage-hud` is a local real-time usage HUD for Codex App. It reads local Codex JSONL / SQLite logs and shows session tokens, cache hit rate, live cost, day/week budgets, and waiting status inside Codex or the Tk fallback window. It does not upload conversation content.

## Quick Start

Download the latest Windows installer from [GitHub Releases](https://github.com/mingbingfeng/codex-usage-hud/releases):

- Windows: `codex-usage-hud-v*-windows-x64-setup.exe`

After installation, Start Menu shortcuts are available:

- `Codex Usage HUD`: daemon entry that waits for Codex App and attaches the HUD.
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

[Want to be listed below?](mailto:512145547@qq.com?subject=codex-usage-hud%20Sponsor)

| Sponsor | Description |
| --- | --- |

## Community and Support

Community and support: coming soon.

If this HUD saves you time while checking token usage and cost, you can support ongoing maintenance with the bundled Alipay or WeChat QR codes.

<p>
  <img src="src/codex_usage_hud/assets/sponsor_alipay.jpg" alt="Alipay QR code" width="210">
  <img src="src/codex_usage_hud/assets/sponsor_wechat.jpg" alt="WeChat reward QR code" width="210">
</p>

## Main Features

- Renderer-first HUD: when Codex exposes a local CDP target, the HUD renders inside Codex App.
- Tk fallback: when CDP is unavailable, the local Tk HUD remains available.
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

![Version and update settings](docs/images/codex-usage-hud-v1-update.png)

Older script-based startup and shutdown flows were awkward. v1.0.0 adds a Windows installer, Start Menu daemon shortcut, stop shortcut, and update shortcut so daily use no longer depends on remembering Python commands.

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

## Data Locations

- Codex session logs: `~/.codex/sessions/`
- Codex SSE / state databases: `~/.codex/logs_2.sqlite`, `~/.codex/state_5.sqlite`
- HUD settings: `%LOCALAPPDATA%\codex-usage-hud\hud_settings.json`
- HUD daemon log: `%LOCALAPPDATA%\codex-usage-hud\daemon.log`
- Default install directory: `%LOCALAPPDATA%\Programs\codex-usage-hud`

## FAQ

### Are the old v0.x tags still recommended?

No. `v0.1.0`, `v0.2.0`, and `v0.3.0` remain as historical alpha / preview tags. `v1.0.0` is the first supported Windows installer release.

### The HUD does not appear inside Codex

Start it from `Codex Usage HUD` or `codex-hud --daemon`. Renderer injection requires a local Codex CDP target. If unavailable, the HUD falls back to the Tk window.

### Does it upload prompts or logs?

No. The project reads local logs and databases only. It does not send telemetry, upload prompts/responses, or require a cloud account. Read [docs/PRIVACY.md](docs/PRIVACY.md) before sharing issue screenshots or logs.

## Development

```powershell
python -m compileall -q src tools tests
python -m unittest discover -s tests
python tools/build_exe.py
python tools/build_installer.py
```

Project structure:

```text
src/codex_usage_hud/
  cli.py                 CLI, daemon, and update command entry
  daemon.py              Windows Codex process listener
  ui/renderer_hud.py     Codex renderer-injected HUD
  ui/tk_hud.py           Tk fallback HUD
  updater.py             GitHub Release update check and installer launch
tools/
  build_exe.py           PyInstaller single-file exe build
  build_installer.py     Inno Setup installer build
  installer/             Inno Setup script
tests/                   Parser, UI, daemon, packaging, and updater tests
```

## Notes

`codex-usage-hud` is an external local monitoring tool. It does not modify Codex App installation files. Codex App or log format changes may require parser or renderer-injection updates.
