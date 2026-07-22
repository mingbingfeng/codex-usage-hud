# Implementation Plan

1. Extend daemon process lifecycle support.
   - Preserve the Windows listener behavior.
   - Add a platform-selected macOS listener and matching tests.
   - Keep process-arrival provenance in `run_daemon`, not global persisted state.
2. Extend renderer startup planning.
   - Add observed-attach and observed-relaunch plan states.
   - Audit verified Desktop process command lines and reject conflicting ports.
   - Keep ordinary startup classification unchanged when provenance is absent.
3. Wire runtime transitions.
   - Pass fresh-launch provenance through `run_hud_session()` and
     `run_renderer_hud_session()`.
   - Give observed CDP launches cold-start readiness bounds and their exact port.
   - Handle observed plain launches with one passive, automatic restart transition.
4. Add regression tests.
   - Planner tests for one port, no port, conflicting ports, and audit failure.
   - Daemon loop tests for external attach and exactly-once auto takeover.
   - macOS listener/process matching tests.
   - Existing three-scenario tests remain unchanged and green.
5. Update runtime contracts and user-facing startup documentation.
6. Verify in order:
   - Focused daemon/startup tests.
   - `python -m pytest tests/test_renderer_hud.py tests/test_active_session.py tests/test_ui.py -q`
   - `python -m pytest`
   - `python -m compileall -q src tests tools`
   - `git diff --check`
   - Windows live acceptance for external plain and external CDP launches.

## Risk And Rollback Points

- `src/codex_usage_hud/cli.py`: startup-plan and daemon-loop branching. Keep new
  states provenance-gated to avoid changing existing launch semantics.
- `src/codex_usage_hud/daemon.py`: platform listener selection. Preserve current
  Windows tests and fail closed on unsupported process inspection.
- Live acceptance stops/relaunches Codex Desktop. Run only after automated gates
  pass, record current CDP/HUD state first, and verify the exact process family.
