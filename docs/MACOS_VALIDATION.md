# macOS Validation

## Current Path

This repository currently uses GitHub Actions `macos-latest` for macOS smoke coverage:

- Workflow: `.github/workflows/macos-smoke.yml`
- Trigger: `workflow_dispatch`, `push`, `pull_request`
- Goal: verify installability of `codex-usage-hud[desktop-overlay]`, lazy CLI imports, source compilation, and task-related tests

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
4. task-related pytest coverage for renderer HUD, desktop overlay branching, and macOS launch argument behavior

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

### Evidence to keep

- one screenshot or screen recording of running bubbles
- one screenshot or screen recording of completion bubbles
- one screenshot or short note showing click-to-switch succeeded
- one short note confirming helper cleanup and state-file cleanup

## Current Decision

For this project state:

1. Keep GitHub Actions smoke as the default low-cost macOS gate.
2. Do not use a remote Mac in the current round.
3. Treat macOS desktop interaction as pending real-device confirmation.
4. Only evaluate hosted Mac options later if macOS packaging, signing, or release support becomes a continuing requirement.
