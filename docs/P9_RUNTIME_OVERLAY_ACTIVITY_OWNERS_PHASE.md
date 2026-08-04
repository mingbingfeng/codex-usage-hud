# P9 Renderer Runtime, Overlay, And Activity Owners

Date: 2026-08-03

Status: structural owner slices complete; automated and package gates pass; real
Windows/macOS acceptance remains open.

## Scope

P9 continues the P0-P8 decomposition with three independent owner boundaries:

1. renderer session assembly and resource registration;
2. detached overlay helper supervision policy;
3. Renderer top-level task/activity projection.

The changes preserve the Renderer/CDP product direction, the existing public
payload ABI, overlay state/command contracts, and reverse-order resource
shutdown. No Qt/Tk product behavior was added.

## Ownership Changes

| Owner | Before | After | Responsibility |
|---|---:|---:|---|
| `renderer_runtime.py` | 423 lines | 409 lines | Startup/window/CDP attachment, worker/event-loop wiring, and final shutdown |
| `renderer_runtime_assembly.py` | not present | 263 lines | One-shot context/overlay/resource and bridge/client/session-adapter assembly |
| `desktop_overlay.py` | 866 lines | 857 lines | Helper process, file watcher, state/diagnostic writes, Qt lifecycle, revision/order |
| `overlay_supervision.py` | not present | 187 lines | Pure helper health, availability, keep-alive, backoff, and command classification |
| `renderer_payload_builder.py` | 1802 lines | 1560 lines | Final payload envelope and compatibility wrappers |
| `renderer_activity_projection.py` | not present | 622 lines | Pure snapshot-to-top-details task/activity/trail projection |

`renderer_runtime_assembly.py` registers each resource immediately with the
existing `RendererSessionResources`; the composition root retains final reverse
close. `overlay_supervision.py` has no subprocess, threading, file-watcher, or
Qt dependency. `renderer_activity_projection.py` receives formatting and
snapshot callbacks through `ActivityProjectionContext` and has no runtime,
CDP, client, or overlay dependency.

## Validation

The current worktree passed:

```text
python -m pytest -q                         PASS (existing skips only)
python -m compileall -q src tests tools     PASS
git diff --check                             PASS
python tools/check_facade_patch_inventory.py PASS (0 paths, 0 references)
targeted P9 owner Ruff                      PASS
```

Focused owner tests passed for runtime assembly, overlay supervision and
activity projection, together with the existing runtime, overlay, payload,
client, presenter, architecture, and boundary suites.

## Package Proof

The P9 source changes were rebuilt and smoke-tested:

- wheel: `tmp/final-wheel-20260803-p9/codex_usage_hud-1.0.5-py3-none-any.whl`
- wheel SHA-256: `678489f5a5f2474107301684681f68a8768c3d989e3ee8583cc89eb17a08993d`
- onefile: `tmp/final-pyinstaller-20260803-p9/dist/codex-hud-p9.exe`
- onefile SHA-256: `50741b212a56cb1f973c9dd76ca370138bfd3260a1309720eacd3f6feb2f907f`

The fresh wheel probe resolves all P7.2-P7.7 and P9 owners from its venv
`site-packages`, exposes the explicit CLI/Renderer allowlists, reports 18
ordered Renderer assets, and preserves the 623712-byte template with SHA-256
`d5ac66c89e93efb2ca3538105da5d5e785a9116712a92dedc78e02cd53eaa899`.
The recursive PyInstaller archive contains the three P9 owners and
`renderer_assets`; `--help` exits 0 with 4315 stdout bytes and 0 stderr bytes.

## Remaining Gates

P9 does not close live product acceptance. The active-session visible latency
still exceeds the `<150ms` target, current-request latency is unmeasured, the
available 60-second idle sample is invalidated by watched file changes, native
Windows theme/drag/resize is unverified, and macOS package/startup/Renderer
evidence is absent. These remain separate from the structural and package
proof above.
