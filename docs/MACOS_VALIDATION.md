# macOS Validation

## Current Path

This repository currently uses GitHub Actions `macos-latest` for macOS smoke coverage:

- Workflow: `.github/workflows/macos-smoke.yml`
- Trigger: `workflow_dispatch`, `push`, `pull_request`
- Goal: verify installability of `codex-usage-hud[desktop-overlay]`, lazy CLI imports, source compilation, and task-related tests
- Latest verified run: `28283179999` on 2026-06-27, status `success`

This smoke workflow is intentionally limited. It does **not** prove real desktop behavior such as:

- PySide6 overlay always-on-top behavior
- dismiss persistence on macOS
- click-to-activate session switching
- renderer HUD injection against a real local Codex App window
- helper subprocess cleanup after interactive use

## GitHub Actions Smoke

### What it runs

1. `python -m pip install -e ".[desktop-overlay]"`
2. lazy import check for `codex_usage_hud.cli`
3. `python -m compileall -q src tests tools`
4. task-related pytest coverage for renderer HUD, session-management contracts,
   desktop overlay branching, and macOS launch argument behavior

### How to use it

1. Push a branch or open a pull request that touches `src/`, `tests/`, `tools/`, `pyproject.toml`, or the workflow file.
2. Or trigger `macOS Smoke` manually from the GitHub Actions UI.
3. Confirm all workflow steps pass before treating macOS code compatibility as green.

## Manual Validation Checklist

Use this checklist later when a real Mac environment is available. It is intentionally parked for future use; this project is not using a remote Mac in the current round.

### Setup

```bash
python -m pip install -e ".[desktop-overlay]"
codex-hud --daemon
```

### Required checks

- Codex App starts or attaches in renderer/CDP mode.
- Renderer HUD injects into the Codex App window.
- Running sessions show square desktop bubbles.
- Completed sessions collapse into circular completion bubbles.
- Dismissed bubbles do not reappear unexpectedly.
- Clicking a bubble triggers `activateSession` and switches to the target session.
- Stopping the HUD cleans up the helper subprocess and overlay state files.

### Session management

- Open `会话管理` in the real renderer and confirm it directly shows the existing
  session list and filters, with no secondary cleanup switch or horizontal overflow.
- Scan sessions and confirm current, running, unresolved, and unavailable sessions
  remain visible but cannot be selected.
- On disposable fixture sessions, generate a deletion preview and verify a
  user-selected backup directory, backup integrity, cutoff deletion, VACUUM,
  post-check, and failure restoration. Do not use a real user database for this
  check without separate explicit authorization.
- With an independent Codex CLI running, confirm offline SQLite maintenance is
  blocked without terminating that CLI. With only an idle Codex App running,
  confirm normal `osascript` quit and one matching app/HUD restart.
- Scan sessions and confirm roots are listed once with descendant counts. The
  current/running session family must remain unselectable. Capability probing
  must require `codex delete --help` to expose `--force`.
- In a disposable `CODEX_HOME`, delete active-list and archived root/child
  fixtures through `codex delete --force`; verify state DB, session index, and
  active/archived rollouts. Never perform this check on real user sessions.

### Evidence to keep

- one screenshot or screen recording of running bubbles
- one screenshot or screen recording of completion bubbles
- one screenshot or short note showing click-to-switch succeeded
- one short note confirming helper cleanup and state-file cleanup
- one screenshot of the cleanup tiers, one screenshot of protected session
  rows, and one redacted helper result from disposable fixtures

## Current Decision

For this project state:

1. Keep GitHub Actions smoke as the default low-cost macOS gate.
2. Do not use a remote Mac in the current round.
3. Treat macOS desktop interaction as pending real-device confirmation.
4. Only evaluate hosted Mac options later if macOS packaging, signing, or release support becomes a continuing requirement.
