# P11 Renderer Event, Overlay Channel, And Request Projection Owners

Date: 2026-08-03

Status: structural owner slices complete; automated, dependency, and package
gates pass; real Windows/macOS acceptance remains open.

## Scope

P11 continues the post-P10 decomposition with three independent pure or
contract-bounded owners. It does not add a Renderer asset, change the injected
IIFE, alter overlay state ordering, or change the public payload envelope.

## Ownership Changes

| Owner | Before | After | Responsibility |
|---|---:|---:|---|
| `renderer_event_loop.py` | 865 lines | 716 lines | Runtime event sampling, refresh execution, and compatibility re-exports |
| `renderer_event_reduction.py` | not present | 166 lines | Pure event-to-refresh-plan reduction and event-batch coalescing |
| `desktop_overlay.py` | 857 lines | 816 lines | Helper/process, watcher, state, diagnostics, Qt side effects, and adapters |
| `overlay_command_channel.py` | not present | 119 lines | Incremental JSONL command tailing, request-id deduplication, and ack append |
| `renderer_payload_builder.py` | 1560 lines | 1441 lines | Final payload envelope and compatibility wrappers |
| `renderer_request_projection.py` | not present | 380 lines | Pure request/task-round projection, formatting, totals, and row details |

The event reduction owner has no client, file, clock, worker, subprocess, or
shutdown side effects. The overlay command channel owns only versioned JSONL
sidecar I/O; helper process, watcher, state revision/order, Qt, and runtime
action routing remain in `desktop_overlay.py`. The request projection owner
receives formatting/session decisions through an explicit context and leaves
the final `RendererHudPayload` envelope in `renderer_payload_builder.py`.

## Compatibility And Behavior

- `renderer_event_loop.py` explicitly re-exports `RefreshPlan`, `reduce_event`,
  and `reduce_events`; existing imports remain valid.
- `DesktopWorkOverlay.take_commands()` and `acknowledge_command()` remain
  compatibility wrappers with the original offset, deduplication, reset, and
  shutdown ordering.
- Payload builder request/task/round wrappers preserve the existing names and
  `renderer_presenters.request` ABI.
- No Renderer manifest entry, public binding, storage key, payload envelope,
  observer/timer cleanup, or no-event work rule changed.

## Validation

The current worktree passed:

```text
python -m pytest -q                         PASS (existing skips only)
python -m compileall -q src tests tools     PASS
git diff --check                             PASS
python tools/check_facade_patch_inventory.py PASS (0 paths, 0 references)
targeted P11 owner Ruff checks              PASS
```

Focused coverage includes event reduction/re-export and architecture checks,
overlay command-channel partial-tail/deduplication/truncation/ack contracts,
the Qt real-helper test, and direct-vs-wrapper request projection behavior.

## Package Proof

The P11 source changes were rebuilt and smoke-tested:

- wheel: `tmp/final-wheel-20260803-p11/codex_usage_hud-1.0.5-py3-none-any.whl`
- wheel SHA-256:
  `5e3b0c5a6288ae4ed250298bfdb935d13ba95c3125364bc04b7f7ee89bcde024`
- onefile: `tmp/final-pyinstaller-20260803-p11/dist/codex-hud-p11.exe`
- onefile SHA-256:
  `8f4afebb624c3f32dc20828cac54449fe39fd50ac477858b9d5f7727a4e5562c`

The isolated wheel probe resolves all three P11 owners from `site-packages`,
reports 18 ordered assets ending with `15_router`, and preserves the
623712-byte template hash and 623684-byte empty-catalog bundle hash. The
recursive PyInstaller archive contains all three P11 owners and
`renderer_assets`; `--help` exits 0 with 4315 stdout bytes and 0 stderr bytes.

## Remaining Gates

P11 does not close live product acceptance. Active-session visible latency is
still above `<150ms`, current-request latency is unmeasured, the available
60-second idle sample is invalidated by watched file changes, native Windows
theme/drag/resize is unverified, and macOS package/startup/Renderer evidence
is absent.
