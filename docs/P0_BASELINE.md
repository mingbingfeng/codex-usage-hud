# P0 Baseline Record

Captured: 2026-07-30 (Asia/Shanghai)

## Provenance

- HEAD: `ddfe29dc72aee19eeeb3231f118157ad557fe16b`
- branch: `main`
- clean archive: `git archive --format=zip HEAD`
- clean archive SHA-256: `b0e62b26d5f8b38e686c24641f426ed4fc57b38a7c5973a18106211225f914c2`
- worktree state: dirty; the active refactor includes tracked and untracked files
- worktree tracked diff SHA-256 at initial P0 capture: `5b84744bf7d28f12a67094190e2a3ecfb54df108129a738b2c1a1cd02ed5dac3`
- worktree porcelain SHA-256 at initial P0 capture: `3d2ed6fb1ae935879b0fc97882c17cef30b732f64d692e2e402b48b9dbf541cc`

The two worktree hashes are provenance aids, not a complete content tree hash. The
current workspace and Git evidence remain authoritative.

## Environment

- Windows 11 `10.0.26200`, x64
- Python `3.14.3` (CPython, MSC v.1944, 64-bit)
- pytest `9.0.3`
- PySide6 / Qt `6.11.1`
- locale `Chinese (Simplified)_China`, preferred encoding `cp936`
- timezone `Asia/Shanghai` / China Standard Time (UTC+08:00)

## Focused Baseline Command

The archive run used an extracted `git archive HEAD` as cwd and fixed pytest's root:

```text
python -m pytest --rootdir . <four exact overlay nodeids> <Qt retry nodeid> -vv --tb=short
```

The same exact node IDs were run in the working tree. Both sources produced the
same four deterministic failures and the Qt retry candidate passed. The exact
node IDs and normalized fingerprints are checked in at
`tests/contracts/p0_confirmed_failures.json`. The Qt candidate is separate in
`tests/contracts/p0_flaky_candidates.json` and is never release-allowlisted.

## Stable Phase Commands

```text
python -m pytest -m "not ui and not qt_ui" -q
python -m pytest -m qt_ui -q
python tools/check_p0_baseline.py --results <normalized-results.json>
```

No command uses `-k not`, a wildcard exclusion, or failure-count matching.

## Formal Repeated Capture

The strict runner completed clean HEAD and worktree runs 1-3 again after the
final P0 source/test changes on 2026-07-30:

- capture directory: `%TEMP%\codex-hud-p0-formal-v7b`
- summary SHA-256: `59d4fed8808cac2f2fdc2cd0f584ccdcd2e37e01da7f31b5d4aa0697afe78af9`
- worktree content SHA-256: `95770d339f82c722567c9d80dfb96a6c5fab4eeb908cccc438dbd7938e6824ce`
- worktree content manifest SHA-256: `09473074001f0532c34d913fdaa1d8630929fa799769c5328a08ad3e0c8d978e`
- Git status SHA-256: `8a30f2d63572a9b40e0dceb0d1696c8111095ca92f1c0ae7b5482b31b71426b1`
- tracked binary patch SHA-256: `66a25f2ae866e1b2bf1042b12a9d941d0974d0d6a8405e0e1108037375edb4c2`
- clean archive SHA-256: `b0e62b26d5f8b38e686c24641f426ed4fc57b38a7c5973a18106211225f914c2`
- every run exit matrix: `[1, 1, 1, 1, 0]`
- `python tools/check_p0_baseline.py --results <summary.json>`: passed

The checker now rejects non-summary input, applies the requested phase to expiry,
requires the exact run-index set, validates manifest schemas and fingerprint hashes,
recomputes artifact and content-manifest hashes, and proves package plus CLI origins
are inside each recorded source root. The v7b capture additionally freezes Git file
mode/symlink topology and stores hash-checked porcelain-status plus full binary patch
artifacts. Artifacts remain outside the repository.

## P0 Exit Evidence

- Stable non-UI gate produced only the four exact confirmed overlay failures. It
  produced no new failure, changed fingerprint, XPASS, or flaky-candidate failure.
- Subprocess Qt gate passed 2 deterministic helper tests; 38 removed legacy Qt
  product tests were explicitly skipped.
- Architecture, runtime ports/boundaries, facade inventory, and baseline gates:
  39 passed.
- Facade patch inventory: 80 classified paths / 443 references. Renderer-session
  construction-resource patches are permanently held at zero.
- Renderer contract: SHA-256
  `f83d65265c55143fa775f509f09ce109c2aa75a03946b42cf462cb6cbd4ea637`,
  548705 bytes, five globals, five bindings, three storage keys, frozen payload
  order, and lifecycle inventory. The checked Windows screenshot SHA-256 is
  `7b5ef938f0aadc304bcf186f4215c339ad3854d6ef9d8ec359e84f9097236a05`.
- Final P0 tree checks passed compileall, changed production/P0 Ruff scope, facade
  inventory, and `git diff --check`.

P0 exits with the four deterministic overlay failures still expiring at P2. P1 may
begin; those entries cannot be broadened and must be removed by the P2 exit gate.

## Package Proof

An isolated `python -m pip wheel . --no-deps` build completed on 2026-07-30 with
full output redirected to `%TEMP%\codex-hud-p0-wheel\build-wheel.log`.

- wheel: `codex_usage_hud-1.0.5-py3-none-any.whl`
- SHA-256: `923633a7ed4765e6353edfc92ce2616733f20332ab62e1fb00f674fc4304b1ce`
- verified contents: all four sponsor images, `cli.py`, `ui/renderer_domains.py`,
  and `ui/renderer_script.py`

The architecture gate also reads every inventoried image through
`importlib.resources` and compares its bytes with the source checkout.
