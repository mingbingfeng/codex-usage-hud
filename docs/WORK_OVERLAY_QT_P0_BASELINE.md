# Work Overlay Qt P0 Baseline

Captured: 2026-08-04 (Asia/Tokyo workspace date; host logs use UTC+08:00).

## Scope

This is the pre-P1 baseline for the mechanical extraction of the pure
constants, model, geometry, and theme owners. It does not authorize visual or
runtime behavior changes.

- HEAD: `60142c42bf5926bce85085a14a7b92c6005f0329`
- Branch: `main`
- Pre-P1 `work_overlay_qt.py` size: 5,959 lines
- Renderer/CDP remains the only main HUD; PySide6 remains the desktop bubble helper.
- Frozen pure-output artifact: [WORK_OVERLAY_QT_P0_PURE_OUTPUTS.json](WORK_OVERLAY_QT_P0_PURE_OUTPUTS.json)

## Real helper captures

All commands used unique state files to avoid reusing a stale Windows watcher
handle.

- `tools/demo_work_overlay_transition.py --auto --once --scale 1 --speed 1`
  completed steps 1/5 through 5/5 and closed the helper.
  Log: `%TEMP%\\codex-hud-p0-auto-20260804.log`
  SHA-256: `C910407DAE924B1AE13E846AD0994D7A8DF8752A83C44147AE1AB0A6580033FA`
- Interactive real helper, `--auto-dismiss C1`, recorded completed-circle check
  click, energy-ring annihilation, opacity decay to zero, and final removal.
  Log: `%TEMP%\\codex-hud-p0-dismiss-20260804.log`
  SHA-256: `61729C048BBCD65BB600FF491B70C21AE411E2F55C2B15A885E1299CD22`
- Interactive real helper, `--auto-pending C1 --auto-pending-complete-ms 1400`,
  recorded `circle_pending.start` then `circle_pending.complete`.
  Log: `%TEMP%\\codex-hud-p0-pending-complete-20260804.log`
  SHA-256: `5CDC73A2B93B0C465CC309189CF6A79ACCA0A03DD33D9ED39E069F3D5574862C`

A first run using the demo's shared default state path hit Windows
`PermissionError [WinError 5]` during atomic replace. Repeating with unique
state paths passed; no product source was changed for this demo-tool race.

## Frozen behavior

- Card to completed: shrink 220 ms, pause 140 ms, move 280 ms, shift 240 ms.
- Completed to card: fade-out 200 ms, shift 300 ms, fade-in 150 ms, descend
  400 ms.
- Completed dismiss: energy-ring annihilation 1,200 ms.
- Shimmer timer: 30 ms.
- Switch pending: timer 120 ms, launch 0.45 s, finish 0.85 s, timeout 45 s.
- Transition animation flag remains `True`.
- The pure snapshot freezes transition detection/defer order, slot rectangles,
  interpolation, clearance trajectory, pending curves, and transition palette.

## Gates

- Focused overlay tests: passed.
- `tests/test_ui.py -k "work_overlay or transition"`: passed.
- `python -m pytest -m ui`: exit 0; 38 skipped, 1,241 deselected.
- Full `python -m pytest -q`: exit 0.
- `python -m compileall -q src tests tools`: passed.
- `git diff --check`: passed.
- CLI/Renderer import smoke: `IMPORT_SMOKE_OK`; neither PySide6 nor
  `codex_usage_hud.ui.work_overlay_qt` was loaded.

P1 may begin. The real-helper captures above are the comparison baseline; any
visible animation or hotspot difference stops the mechanical extraction.
