# Handoff: PySide6 work-bubble workdir regressions

Handoff time: 2026-07-17 14:02:44 +08:00 (Asia/Shanghai)

Current task: repair the three workdir hover regressions in the PySide6 desktop work bubble.

Module: `src/codex_usage_hud/ui/work_overlay_qt.py` and its focused tests.

This handoff fully replaces the previous handoff.

## 1. Current project goal

Keep renderer mode as the product path while maintaining the standalone PySide6
desktop work bubble. The current narrow goal is to restore the pre-hover visual
and interaction behavior of workdir text without giving any provider special
treatment.

## 2. Current problem

The user rejected the latest fix for these live UI regressions:

1. A rectangular card's workdir is ellipsized (example screenshot shows
   `...moon`).
2. A completed circular badge shows an unwanted independent rectangular workdir
   layer rather than only the original arc-aligned workdir text.
3. The completed badge checkmark cannot be clicked to trigger the existing
   close/annihilation effect.

The supplied screenshots were in the prior conversation only; they are not in
this workspace. Do not claim a visual fix without a real helper restart and
user-visible verification.

## 3. Completed changes

- Earlier changed hover opacity: `WORK_OVERLAY_HOVER_ALPHA` in `cli.py` is now
  `0.22` (working-tree change, not committed).
- Added high-visibility hover behavior to close/workdir top-level windows in
  `work_overlay_qt.py` (working-tree change, not accepted by the user).
- Added multi-provider transition serialization so a second simultaneous
  card/badge shape change is deferred instead of being silently marked applied.
  This passed tests and was accepted before the later workdir regression report.
- Latest, unaccepted attempt restored the card's normal QLabel color, removed
  the completed badge workdir anchor, made the external workdir layer card-only,
  and expanded the hover window to a text minimum width. The user explicitly
  said these three repairs were "not very satisfactory". Treat this code as a
  failed/partial attempt, not as the desired design.

## 4. Key files

- `src/codex_usage_hud/ui/work_overlay_qt.py`
  - `WorkdirLinkWindow`: independent top-level hover/click window.
  - `_build_completed_row` / `_update_completed_badge`: completed circle arc
    text and checkmark hotspot setup.
  - `_build_item_card` / `_update_item_card`: rectangular card workdir label
    and workdir anchor creation.
  - `reposition_interactive_windows`: creates/positions top-level close,
    workdir, and completed-check windows.
  - `_pending_workdir_window_rect`: anchor geometry helper; latest attempt
    added `minimum_width`.
- `tests/test_ui.py`: pure geometry/helper and work-overlay transition tests.
- `.trellis/spec/backend/renderer-runtime-contracts.md`: contains concurrent
  renderer-session work from another stream plus this session's overlay notes;
  do not overwrite wholesale.
- `docs/WORK_ACTIVITY_OVERLAY_DESIGN.md`: likely best workspace-local design
  reference before changing the visual behavior.

## 5. Current Git status summary

At handoff, tracked modified files:

- `.trellis/spec/backend/renderer-runtime-contracts.md`
- `src/codex_usage_hud/cli.py`
- `src/codex_usage_hud/core/parser.py`
- `src/codex_usage_hud/platforms/active_session.py`
- `src/codex_usage_hud/ui/renderer_hud.py`
- `src/codex_usage_hud/ui/work_overlay_qt.py`
- `tests/test_active_session.py`
- `tests/test_renderer_hud.py`
- `tests/test_ui.py`

Untracked task directory:

- `.trellis/tasks/07-17-renderer-session-follow-reliability/`

Most changed renderer/session files belong to the active renderer-session-follow
workstream. Preserve them. Only `work_overlay_qt.py`, the small related parts of
`tests/test_ui.py`, and the appended overlay section in the runtime contract are
in scope for the workdir issue.

## 6. Diff-stat summary

`git diff --stat` at handoff reported 9 changed files, 2122 insertions and 195
deletions. The scope is broad because parallel renderer-session work is present.
For the workdir issue, do not use a whole-file replacement or reset.

## 7. Important diff changes

### `work_overlay_qt.py`

- New `WORK_OVERLAY_HOTSPOT_HOVER_ALPHA = 0.96` and hover opacity preservation
  for `CloseButtonWindow` / `WorkdirLinkWindow`.
- `WorkdirLinkWindow` now stores and draws its own label on hover. This is the
  likely source of the visible duplicate/ellipsis behavior and must be reviewed
  against the pre-change implementation using `git show HEAD:...`.
- `_workdir_external_link_for_item()` currently prevents an external workdir
  layer for completed items. The user still rejected the net visual result.
- `_pending_workdir_window_rect()` gained `minimum_width` and aligns expanded
  hover bounds from the anchor's right edge. This needs live validation; it may
  enlarge the hit area over adjacent footer content.
- Multi-provider transition helpers `_transition_changes()` and
  `_defer_other_transition_items()` are separate from the workdir problem.
  Preserve them unless real evidence shows otherwise.

### `tests/test_ui.py`

- New tests cover deferred multi-provider shape transitions, completed workdir
  external-layer eligibility, and expanded hover geometry. They are logical
  tests, not a substitute for visual/PySide interaction validation.

### Runtime contract spec

- New/modified renderer-session content may belong to the parallel active task.
- The appended `Concurrent work-overlay shape transitions` section documents
  the multi-provider transition queue. Do not remove it while repairing the
  workdir UI unless the underlying behavior changes deliberately.

## 8. Attempted optimizations and results

| Attempt | Result |
|---|---|
| Lower bubble hover opacity from 0.52 to 0.22 | Partially effective visually; not the current blocker. |
| Give close/workdir top-level windows 0.96 opacity on their own hover | Close behavior was intended; workdir visual regression followed. |
| Draw normal workdir text in `WorkdirLinkWindow` | Failed: screenshot showed ellipsized rectangular workdir and duplicate visible circle workdir layer. |
| Restore card QLabel normal text; only draw external text while hovered | Unverified visually and rejected as a net three-item repair. |
| Remove completed badge workdir anchor and make external layer card-only | Logically addresses duplicate/click interception, but user reported the full repair remains unsatisfactory. |
| Expand hover window to minimum text width | Unit-tested only; not visually verified. |
| Serialize multiple card/badge transitions | Successful test-level fix for multi-provider simultaneous shape changes; retain. |

## 9. Possible next directions

1. Revert only the workdir-hover experiment to the known pre-change implementation
   using a surgical diff against `HEAD`, while preserving the separate
   multi-provider transition helpers and the 0.22 overall hover opacity if still
   desired.
2. Then design hover affordance from the actual rendered layout, not by drawing a
   second copy of label text. Candidate: keep the existing QLabel as the only
   text renderer and use an external window solely for a tightly bounded
   background/underline or temporary opacity effect.
3. For completed circles, do not create any workdir top-level window. Preserve
   the badge's own arc painter and `ClickHotspotWindow` check region.
4. Run the source helper and manually test the exact three screenshots/states
   before delivering.

## 10. Next minimal execution plan

1. Read this file and inspect the exact current diff of only
   `work_overlay_qt.py` and relevant tests.
2. Compare those hunks with `git show HEAD:src/codex_usage_hud/ui/work_overlay_qt.py`
   to isolate all workdir-hover changes from the multi-provider animation change.
3. Restore the original rectangular and circular workdir renderers first; do not
   attempt a new hover visual yet.
4. Start/restart the source work-bubble helper and visually verify:
   - rectangular `zjxc.moon` text at rest and on hover;
   - completed circle only has arc-aligned workdir text;
   - completed checkmark click triggers the existing close effect.
5. Only after those pass, add the smallest hover affordance that does not draw
   duplicate text or expand the click target over unrelated footer content.
6. Add a focused test for any pure helper change, run full `tests/test_ui.py`,
   compileall, and `git diff --check`.

## 11. Unresolved issues

- The user rejected the latest three-item repair. Exact visual reason beyond
  the supplied screenshots is not recorded.
- No real helper restart/interactive test was performed after the last code
  edits; prior checks were unit/compile/diff only.
- It is currently unknown whether a top-level workdir window can provide a
  satisfactory hover affordance without altering the original QLabel/circle
  painter layout.

## 12. Risks, forbidden changes, and notes

- Do not reset, checkout, or overwrite the dirty worktree.
- Do not alter the active renderer session-follow changes in `cli.py`,
  `active_session.py`, `renderer_hud.py`, parser, or their tests.
- Do not remove the multi-provider animation serialization without replacing
  its regression coverage.
- Do not move renderer product behavior to Qt/Tk; this is narrowly legacy
  PySide6 work-bubble maintenance.
- Do not claim visual completion based solely on pure unit tests.
- The old screenshots were outside this workspace and are not available to a
  new session through this handoff skill; request/reuse screenshots from the
  user if needed.

## 13. Acceptance criteria

- Rectangular workdir remains fully readable at rest and when hovered; no
  duplicate string and no unintended left ellipsis.
- Completed circle contains only its original arc workdir text; no rectangular
  overlay window/text is visible.
- Completed checkmark is clickable and invokes the original dismissal effect.
- Existing single-provider and multi-provider card/badge transition tests keep
  passing; no provider gets special visual behavior.
- Real source-helper validation covers all three conditions.

## 14. Key data and verification history

- User supplied two screenshots in the prior session:
  - rectangular card showed `...moon` (workdir truncation);
  - completed circle showed an independent text layer in addition to its arc
    text.
- The prior 3-item code attempt passed:
  - full `python -m pytest tests/test_ui.py -q`: 273 tests passed;
  - focused work-overlay tests: 53 passed;
  - `python -m compileall -q src tests tools` passed;
  - `git diff --check` passed.
- Those results do not prove live layout/hit-testing.
- The current broad worktree also contains renderer-session-follow work with
  real acceptance notes in its task artifacts; preserve it.
