# codex-usage-hud v1.0.1 · Archived Sessions Summary Patch

> Release body for GitHub Releases.

## TL;DR

`v1.0.1` is a patch release that keeps the `v1.0.0` Windows installer line and
fixes session discovery so the HUD scans the sibling `archived_sessions` tree,
keeps polling after event gaps, and removes a redundant Tk header close button.

## Fixed

- `UsageSummaryCache` now treats `archived_sessions` as a sibling scan root
  when `sessions` is the active session tree.
- Active-session detection now considers archived session trees too, and the
  resolver can still fall back cleanly when tracker state is unmatched.
- The realtime watcher keeps issuing backstop polls after event gaps so title
  changes still land when event signals go quiet.
- The Tk expanded header no longer shows a redundant close button.
- Added regression coverage so day/week totals stay correct when archived logs
  sit outside the live folder, and the active-session / Tk layout changes stay
  covered.

## Verification

```powershell
python -m unittest discover -s tests
python -m compileall -q src tools tests
python tools/pre_release_check.py
```

## Notes

- Windows installer asset naming remains
  `codex-usage-hud-v1.0.1-windows-x64-setup.exe`.
- Runtime behavior remains local-first: no telemetry, no log upload, and no
  prompt/response upload.
