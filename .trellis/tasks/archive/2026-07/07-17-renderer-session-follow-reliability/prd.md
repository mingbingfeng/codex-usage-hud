# 修复 HUD 会话秒切换与失败反馈

## Goal

Make renderer-mode HUD session following deterministic and visibly responsive when
the user selects an existing Codex App conversation or creates a new one. The HUD
must show the selected session's primary statistics immediately when local data is
available, converge automatically when Codex publishes persistence metadata later,
and explain any delayed convergence inside the existing HUD surface.

## Background

- The renderer is the active-session authority; native title polling and newest-file
  guessing remain forbidden.
- Live Codex App inspection on 2026-07-17 showed an existing persisted conversation
  whose selected sidebar row still exposed `local:client-new-thread:*`, while both
  `session_index.jsonl` and `state_5.sqlite` already contained its canonical UUID.
- `ActiveSessionTracker.observe_conversation_ref()` currently clears the provisional
  renderer ID when its first constrained lookup misses. A later `session-map` event
  invalidates caches but cannot retry the lost provisional identity.
- The renderer's bounded follow-ups are signature-deduplicated after the first
  binding call and do not have an application acknowledgement.
- The active-session fast path currently forces `active_work_items` rebuilding,
  synchronously parsing up to 16 recent JSONL files. Local measurements showed a
  hot visible-only snapshot around 61-64 ms versus roughly 300-344 ms with the
  recent-work rebuild; daemon logs contain slower Python snapshot outliers.
- Existing uncommitted changes in `src/codex_usage_hud/cli.py` and
  `tests/test_ui.py` belong to a separate CDP restart fix and must be preserved.

## Requirements

- R1. Preserve the renderer's raw selected identity and a monotonic selection
  generation until that selection is superseded, including provisional
  `client-new-thread:*` identities.
- R2. Reconcile the currently selected identity whenever a `session-map` event
  arrives. For a provisional ID, accept a canonical UUID only when exact title
  evidence is unique and consistent between `session_index.jsonl`,
  `state_5.sqlite`, and an existing rollout path. Ambiguous or missing evidence
  must stay pending without selecting another session.
- R3. Prevent stale callbacks, hydration results, mapping events, or renderer
  follow-ups from an older selection from overwriting a newer selection.
- R4. Split visible session switching from active-work aggregation. The first
  update must parse only the selected session through the bounded preview/cache
  path and reuse cached budget/work-overlay data; recent-session work items may
  refresh asynchronously afterward.
- R5. Carry observation timing and selection identity through the renderer/Python
  boundary so latency can be attributed to observation, mapping, snapshot, CDP
  transport, and renderer application.
- R6. When the selected session is still unconfirmed after the fast target, show
  a concise reason in the existing HUD language: awaiting canonical ID, awaiting
  exact local mapping, ambiguous persisted identity, reading session data, or
  renderer event channel unavailable. Normal operation must not require DEBUG.
- R7. Preserve renderer-only architecture, Windows/macOS compatibility, current
  bubble geometry, provider filtering, settings behavior, and existing CDP restart
  work.
- R8. Keep the runtime event-driven. Bounded selection-specific retries are
  allowed; unconditional active-session polling is not.

## Acceptance Criteria

- [x] AC1. A real canonical sidebar selection updates the visible HUD session
  fields with P95 under 150 ms across at least 50 alternating selections, with no
  missed final selection.
- [x] AC2. A persisted conversation exposed as `client-new-thread:*` resolves after
  mapping/index availability without another user click and never displays as a
  true new conversation.
- [x] AC3. A provisional identity with zero or multiple valid persisted matches
  remains pending, never falls back to newest/title-prefix activity, and exposes a
  specific waiting reason within 200 ms.
- [x] AC4. Rapid A -> B -> C selection cannot be overwritten by delayed work for A
  or B; C remains the final applied selection.
- [x] AC5. The visible-first active-session refresh does not call the recent 16-file
  work-item scan. A later event refreshes the bubble list without changing the
  selected session identity.
- [x] AC6. Renderer transport/application acknowledgement is tied to the current
  selection generation; a dropped/failed first delivery produces a visible reason
  and bounded recovery rather than requiring a second click.
- [x] AC7. Focused active-session/renderer/UI tests, compileall, diff check, and the
  renderer latency harness pass. Real Codex App validation records the observed
  click-to-HUD timings and provisional-ID behavior.

## Out Of Scope

- Replacing renderer authority with app-server, native title tracking, or private
  Codex IPC.
- Redesigning HUD/bubble visuals or changing provider/settings product behavior.
- Removing legacy Qt/Tk code beyond changes strictly required by shared tests.
