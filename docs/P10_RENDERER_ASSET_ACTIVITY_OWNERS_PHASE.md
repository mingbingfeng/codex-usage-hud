# P10 Renderer Asset And Activity Owners

Date: 2026-08-03

Status: structural owner slices complete; automated, byte-contract, and package
gates pass; real Windows/macOS acceptance remains open.

## Scope

P10 continues the post-P8 decomposition without adding a manifest asset or
changing the injected Renderer IIFE. It separates static subdomains inside the
existing layout/settings assets and extracts the remaining pure activity trail
projection.

## Ownership Changes

| Owner | Before | After | Responsibility |
|---|---:|---:|---|
| `renderer_activity_projection.py` | 622 lines | 488 lines | Pure top-level task/activity projection and compatibility wrappers |
| `renderer_activity_trail.py` | not present | 202 lines | Pure activity event filtering, merge, deduplication, and trail formatting |
| `renderer_assets/layout.py` | 5583 lines | 14 lines | Single manifest-level layout asset assembly |
| `renderer_assets/layout_style.py` | not present | 4383 lines | Static layout CSS and style bootstrap fragment |
| `renderer_assets/layout_markup.py` | not present | 214 lines | Static layout markup helpers |
| `renderer_assets/layout_gestures.py` | not present | 139 lines | Pointer gesture helpers |
| `renderer_assets/layout_anchors.py` | not present | 470 lines | Geometry, anchoring, and width helpers |
| `renderer_assets/layout_observers.py` | not present | 397 lines | Resize/mutation observers, position sync, and export tail |
| `renderer_assets/settings_shell.py` | 1616 lines | 1514 lines | Settings shell prefix/suffix and domain assembly |
| `renderer_assets/settings_support_panels.py` | not present | 113 lines | Static support/about panel markup fragment |

`layout.py` still contributes exactly one `12_layout` manifest entry and joins
the five fragments in their original byte order. `settings_shell.py` joins its
support/about fragment without changing the `createSettingsShellDomain`
closure, bindings, lifecycle calls, or manifest order. The activity trail owner
has no builder, runtime, client, overlay, or CDP dependency; the projection
module retains the compatibility wrapper used by the payload builder.

## Validation

The current worktree passed:

```text
python -m pytest -q                         PASS (existing skips only)
python -m compileall -q src tests tools     PASS
git diff --check                             PASS
python tools/check_facade_patch_inventory.py PASS (0 paths, 0 references)
targeted P10 owner Ruff                    PASS
```

Renderer asset/leaf/HUD tests prove the fixed 18-item manifest, exact template
length/hash, static-fragment join order, support-panel ownership, and unchanged
binding/lifecycle contracts. Activity owner tests prove direct trail output is
identical to the projection compatibility wrapper.

## Package Proof

The P10 source changes were rebuilt and smoke-tested:

- wheel: `tmp/final-wheel-20260803-p10/codex_usage_hud-1.0.5-py3-none-any.whl`
- wheel SHA-256: `c628c41ff1a04a89d7b9e8e8e92e2ea484b14fadc133868415fd7845900476fb`
- onefile: `tmp/final-pyinstaller-20260803-p10/dist/codex-hud-p10.exe`
- onefile SHA-256: `9267a3b857f502cb434fa85dad2f692d4f85476a0e97b42c443c9e05d5e21b50`

The fresh wheel probe resolves the P7.2-P7.7, P9, and P10 owners from venv
`site-packages`, exposes the explicit CLI/Renderer allowlists, and reports 18
assets ending with `15_router`. The template remains 623712 bytes with SHA-256
`d5ac66c89e93efb2ca3538105da5d5e785a9116712a92dedc78e02cd53eaa899`. The
recursive PyInstaller archive contains the new layout/settings/activity modules;
`--help` exits 0 with 4315 stdout bytes and 0 stderr bytes.

## Remaining Gates

P10 does not close live product acceptance. The active-session visible latency
still exceeds the `<150ms` target, current-request latency is unmeasured, the
available 60-second idle sample is invalidated by watched file changes, native
Windows theme/drag/resize is unverified, and macOS package/startup/Renderer
evidence is absent.
