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
- Stack direction: top to bottom, current session first, then most recently updated active sessions.
- Background: `#0A0F14` around the stack, card `#10161D`.
- Border: 1 px `#263241`.
- Text:
  - Elapsed: Microsoft YaHei UI, 9 px bold, `#A9B6C6`, pinned on the title row.
  - Close: Microsoft YaHei UI, 10 px bold, `#A9B6C6`.
  - Last output: Microsoft YaHei UI, 8 px, `#B8C6D8`.
  - Current status: Microsoft YaHei UI, 8 px bold, status accent color or `#8492A6`.

## Status Colors

- Running: accent `#F3D27A`, pill background `#1C190F`.
- Waiting for user: accent `#FFB86B`, pill background `#1D1610`.
- Tool execution: accent `#9CCBFF`, pill background `#0D1722`.
- Just completed: accent `#8FE3A1`, pill background `#0E1B14`.

## Behavior

- Show one bubble per active Codex work item, including background sessions that keep writing locally after the Codex window is minimized or closed.
- Hide the overlay when there are no active items.
- Keep the overlay topmost and independent of Codex window visibility.
- The title row keeps elapsed processing time and a close button visible.
- The body is ordered like the Codex App turn snapshot: latest assistant output first, current waiting/status text below it.
- Completed bubbles use the green accent and card background only after the current task emits `task_complete`.
- The bubble defaults to partial transparency, becomes more transparent while the cursor is over it, and allows clicks through its content area while keeping the close button clickable on Windows.
- Dismissing one bubble hides only that session until its visible activity changes again.
- Bubble text is compacted instead of scrolling; long body text wraps inside the card.
- The main HUD remains unchanged: Renderer/Tk still display token and budget panels, while the activity overlay is desktop-level.
