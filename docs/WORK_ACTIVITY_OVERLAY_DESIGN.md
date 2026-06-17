# Codex Work Activity Overlay Design

Figma MCP is not available in this session, so this is the Figma-ready source spec for the implemented UI.

## Placement

- Surface: primary screen desktop overlay, independent of the Codex window.
- Default anchor: top-right, 16 px from the right edge, 56 px from the top edge.
- Rationale: stays visible when Codex is minimized, avoids the taskbar and the common input/composer area, and scales vertically when several Codex jobs are active.

## Component

- Name: Work activity bubble stack.
- Width: 430 px.
- Max visible items: default 6, user-configurable in settings.
- Stack direction: completed circular badges first, then rectangular active work cards from top to bottom.
- Background: `#0A0F14` around the stack, card `#10161D`.
- Border: 1 px `#263241`.
- Text:
  - Elapsed: Microsoft YaHei UI, 9 px bold, `#A9B6C6`, pinned on the title row.
  - Close: Microsoft YaHei UI, 10 px bold, `#A9B6C6`.
  - Last output: Microsoft YaHei UI, 8 px, `#B8C6D8`.
  - Current status: Microsoft YaHei UI, 8 px bold, status accent color or `#8492A6`.
- Completed badge:
  - Size: 168 px circular badge inside the 430 px overlay row, right-aligned.
  - Motion: 520 ms ease-out morph from the original wide rectangular bubble footprint into the circular badge.
  - Fill: green gradient from `#49E07D` through `#1FA85A` to `#0A5B35`, with pale green circular strokes.
  - Content: arced session title on the upper rim, arced workdir leaf on the lower rim, central checkmark, elapsed processing time, tokens, cost, and cache hit rate.

## Status Colors

- Running: accent `#F3D27A`, pill background `#1C190F`.
- Waiting for user: accent `#FFB86B`, pill background `#1D1610`.
- Tool execution: accent `#9CCBFF`, pill background `#0D1722`.
- Just completed: green circular badge, close button at the badge's top-right edge.

## Behavior

- Show one bubble per active Codex work item, including background sessions that keep writing locally after the Codex window is minimized or closed.
- Hide the overlay when there are no active items.
- Keep the overlay topmost and independent of Codex window visibility.
- The title row keeps elapsed processing time and a close button visible.
- The body is ordered like the Codex App turn snapshot: latest assistant output first, current waiting/status text below it.
- Completed sessions do not disappear on a timer after `task_complete`; they are kept in the overlay until dismissed or displaced by the configured visible item limit.
- Completed bubbles animate from their wide rectangular footprint into right-aligned green circular badges and are pinned above the rectangular active cards.
- The bubble defaults to partial transparency, becomes more transparent while the cursor is over it, and allows clicks through its content area while keeping the close button clickable on Windows.
- Dismissing one bubble hides that session for the current task and keeps it hidden through completion; it may appear again after the next task starts.
- Bubble text is compacted instead of scrolling; long body text wraps inside the card.
- The main HUD remains unchanged: Renderer/Tk still display token and budget panels, while the activity overlay is desktop-level.
