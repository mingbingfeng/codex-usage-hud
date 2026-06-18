# codex-usage-hud v1.0.2 · Completed Bubble Transition Patch

> Release body for GitHub Releases.

## TL;DR

`v1.0.2` is a patch release that keeps the `v1.0.0` Windows installer line and
fixes the completed-bubble workflow so overlay cards animate on the intended
right-edge path, preserve the richer completed badge details, avoid showing
historical completions at startup, and keep renderer session switching stable
on more local CDP endpoint variants.

## Fixed

- Completed work-overlay cards now shrink in place, rise on the right-side
  vertical track, and return through the same path when a finished task becomes
  active again.
- The settled completed badge again shows the title, runtime, token/cost/cache
  metrics, workdir label, and the existing click targets for close / jump-back.
- Startup filtering no longer surfaces historical completed tasks as fresh
  green completion bubbles before the current runtime has seen them run.
- Work-overlay click commands now drain on a background pump so session
  activation stays responsive while the renderer / Tk HUD keeps repainting.
- The CDP websocket handshake now formats IPv6 and loopback authority/origin
  headers safely for local debugger endpoints.

## Verification

```powershell
python -m unittest discover -s tests
python -m compileall -q src tools tests
python tools/pre_release_check.py
python tools/build_installer.py
```

## Notes

- Windows installer asset naming is now
  `codex-usage-hud-v1.0.2-windows-x64-setup.exe`.
- Runtime behavior remains local-first: no telemetry, no log upload, and no
  prompt/response upload.
