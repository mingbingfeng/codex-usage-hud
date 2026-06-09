# codex-usage-hud v1.0.0 · Windows Installer Edition

> Release body for GitHub Releases.

## TL;DR

`v1.0.0` is the first supported Windows installer release for
`codex-usage-hud`. It keeps the local-first renderer/Tk HUD behavior from the
v0.x preview line, adds version display and GitHub Release update checks, and
ships a Windows setup package named:

```text
codex-usage-hud-v1.0.0-windows-x64-setup.exe
```

## Headliners

### Windows installer

- Adds an Inno Setup 6 installer that installs to
  `%LOCALAPPDATA%\Programs\codex-usage-hud`.
- Creates Start Menu shortcuts for starting the daemon, stopping the HUD, and
  checking updates.
- Offers an optional desktop shortcut and optional startup registration.
- Stops an existing HUD before replacing the executable during install/update.

### Version display and auto update

- Adds `codex-hud --version`, `codex-hud --check-update`, and
  `codex-hud --update`.
- Adds a renderer HUD "版本更新" tab and a Tk fallback "版本更新" tab.
- Checks GitHub Releases, selects the Windows setup asset, downloads it, and
  launches the installer.

### Homepage refresh

- Makes Chinese the default GitHub README and adds `README_EN.md`.
- Keeps an empty sponsors table with the sponsor contact link:
  `512145547@qq.com`.
- Keeps the community/support section as "敬请期待" and uses bundled Alipay /
  WeChat support QR assets.
- Adds v1.0.0 HUD screenshots and a pain-points section focused on token/cost
  transparency, waiting-state visibility, and custom day/week budgets.

## Deprecated historical tags

`v0.1.0`, `v0.2.0`, and `v0.3.0` remain available as historical alpha / preview
tags. New users should install `v1.0.0` or newer from GitHub Releases.

## Verification

```powershell
python -m unittest tests.test_updater tests.test_build_installer tests.test_build_exe tests.test_renderer_hud
python tools/build_installer.py
```

## Notes

- Windows is the only supported installer target in this release.
- Runtime behavior remains local-first: no telemetry, no log upload, and no
  prompt/response upload.
