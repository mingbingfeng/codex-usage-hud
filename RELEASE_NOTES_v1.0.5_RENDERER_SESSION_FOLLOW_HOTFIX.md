# codex-usage-hud v1.0.5 - Renderer Session Follow Hotfix

> Release body for GitHub Releases.

## TL;DR

`v1.0.5` is a Windows-first hotfix for Codex App sluggishness while clicking or
switching conversations. Renderer mode now receives active-session changes
through a CDP binding from the Codex renderer instead of using the Windows
native title/UIA watcher as the default hot path.

## Fixed

- Replaced renderer-mode active-session follow with a CDP binding emitted from
  the in-renderer HUD script, with the existing HTTP bridge left as fallback.
- Suspended the native active-title watcher in renderer mode so Windows UIA is
  no longer polled during normal Codex sidebar/session switching.
- Debounced renderer filesystem change wakeups and throttled active work rescans
  to reduce CPU bursts when session files or settings change.
- Resolved renderer thread ids from `state_5.sqlite` before recursive session
  scans, which avoids unnecessary disk traversal during conversation switches.
- Tightened sidebar click detection so nested buttons still report the parent
  conversation row and strip trailing relative-time text from the session title.

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
  `codex-usage-hud-v1.0.5-windows-x64-setup.exe`.
- Qt/Tk main HUD modes remain deprecated; this fix keeps the product path on
  renderer/CDP.
