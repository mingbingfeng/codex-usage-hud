# Implementation Plan

## Checklist

- [x] Re-read the current `cli.py`, `work_overlay_qt.py`, `test_ui.py`, and
  renderer runtime-contract diffs; preserve the user's unrelated parser and
  work-status changes.
- [x] Add bounded Windows/macOS Codex desktop command-line discovery, remote
  debugging flag parsing, candidate de-duplication, and existing Codex target
  validation.
- [x] Split attach-port selection from launch-port selection. Reuse only a
  verified live Codex endpoint for attach; require a bindable port immediately
  before launch and allocate a fresh one when necessary.
- [x] Add an explicit three-scenario startup plan and route daemon/direct entry
  paths through it, preserving `launched_codex=True` across recovery so no second
  launch occurs.
- [x] Extend `DesktopWorkOverlay` state/signature/lifecycle with a system action
  that is independent of ordinary item enablement and `work_overlay_max_items`.
- [x] Render the persistent restart card and visible click hotspot in
  `work_overlay_qt.py`; emit readiness and idempotent `restartCodex` commands over
  the existing command file.
- [x] Add event-driven startup action waiting and helper-exit detection. Fall back
  to `HudLoadingFeedback` only for missing/unready PySide6 and record the reason.
- [x] Move fresh-port allocation to after the user click and successful Codex
  stop; then launch once and enter Scenario 1.
- [x] Add focused tests for process identity/flag parsing, candidate validation,
  occupied launch ports, all three startup plans, recovery launch carry-forward,
  action persistence, `item_limit=0`, idempotent click, owner cleanup, and fallback.
- [x] Run the real PySide6 event loop offscreen to verify the action card renders,
  remains past the normal stale deadline, exposes a native click hotspot, emits
  one command, and exits cleanly.
- [x] Update `.trellis/spec/backend/renderer-runtime-contracts.md` in English with
  the final startup classifier, process-port evidence, and system-action IPC
  contracts.
- [x] Run focused and full quality gates.
- [x] Treat CLI `compacted` / `context_compacted` as continuation boundaries for
  terminal markers; add parser and user-visible work-overlay regressions from the
  current session JSONL sequence.
- [x] Perform Windows live acceptance for Scenario 3 without restarting Codex.
- [x] Perform Windows live acceptance for Scenario 2 click/restart -> Scenario 1.
  Do not stop the user's current Codex App before the explicit live-verification
  checkpoint.

## Validation Commands

```powershell
python -m pytest tests/test_ui.py -q
python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py -q
python -m pytest
python -m compileall -q src tests tools
git diff --check
```

For PySide6 source-helper validation:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest tests/test_ui.py -q -k "work_overlay and restart"
```

## Live Acceptance Evidence

- 2026-07-18 Scenario 3: source HUD daemon PID `20912` classified `attach` on
  port `59629` from `desktop-process`; `/json/version` reported protocol `1.3`,
  `pick_page_target()` selected `Codex` at `app://-/index.html`, and no system
  restart action appeared.
- 2026-07-18 CLI compaction regression: replaying the current rollout through
  line 1181 produced `final_answer_at=None`, `request.status=running`, and a
  square `active` work item. A post-restart desktop screenshot confirmed the
  live card remained square and displayed `处理中`.
- 2026-07-18 Scenario 2 -> Scenario 1: the user completed the Windows interaction
  and reported no issue. Runtime diagnostics classified `restart-required` at
  `12:46:35` without stopping Codex, recorded exactly one matching restart action
  at `12:46:52`, selected fresh port `58803`, and entered `attach-launched` at
  `12:46:53`. The replacement Desktop PID `23356` was then discovered on the same
  port and classified `attach` at `12:47:27`; there was no repeated prompt or
  second launch stage.
- 2026-07-18 final live probe: port `58803` returned CDP protocol `1.3`; target
  selection found `title=Codex`, `type=page`, and `url=app://-/index.html`.
  `renderer_cdp_state.json` recorded both requested and successful port `58803`.
- 2026-07-18 final quality gate: 18 focused startup/compaction regressions passed,
  the Renderer gate passed, the default suite passed with `649 passed, 130
  deselected`, and `compileall` plus `git diff --check` passed. Changed-scope Ruff
  matched the existing `HEAD` baseline exactly, with no new finding.

- Scenario 3: with the current Codex App on a non-default CDP port, start HUD once;
  verify the port source is the verified desktop process, `/json/list` selects the
  Codex main page, HUD injects, and no restart action appears.
- Scenario 2: launch Codex without CDP, start HUD, leave the PySide6 action visible
  beyond the old stale threshold, and confirm the App remains running. Click the
  visible command once and capture the single restart request.
- Scenario 1: after the Scenario 2 click (and separately from a fully stopped App
  if needed), verify one CDP launch, one requested port, one successful attach,
  and a usable renderer HUD without rerunning HUD.
- Record process IDs/launch count, selected port/source, `/json/version`, the Codex
  target row, and concise log stages. Do not retain full process command lines.

## Risk And Rollback Points

- `tests/test_ui.py` and `.trellis/spec/backend/renderer-runtime-contracts.md`
  already contain unrelated user changes. Patch only local sections and re-read
  the combined diff before verification.
- A process command line is candidate evidence, not sufficient identity. Never
  accept a port without the existing desktop-process filter and Codex page-target
  validation.
- Do not let an explicit or persisted but occupied port break Scenario 1. Launch
  selection must check bindability at the last responsible moment.
- Do not allow ordinary overlay updates, item-limit configuration, stale-file age,
  or close-button behavior to remove a pending system action.
- Do not convert helper waiting into a high-frequency polling loop.
- If live acceptance would stop the user's active Codex work before they click the
  restart action, pause at that checkpoint rather than terminating it directly.
