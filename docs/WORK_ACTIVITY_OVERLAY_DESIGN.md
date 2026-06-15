# Codex Work Activity Overlay Design

Figma MCP is not available in this session, so this is the Figma-ready source spec for the implemented UI.

## Placement

- Surface: primary screen desktop overlay, independent of the Codex window.
- Default anchor: top-right, 16 px from the right edge, 56 px from the top edge.
- Rationale: stays visible when Codex is minimized, avoids the taskbar and the common input/composer area, and scales vertically when several Codex jobs are active.

## Component

- Name: Work activity bubble stack.
- Width: 360 px.
- Max visible items: 6.
- Stack direction: top to bottom, newest/current first.
- Background: `#0A0F14` around the stack, card `#10161D`.
- Border: 1 px `#263241`.
- Text:
  - Title: Microsoft YaHei UI, 9 px bold, `#E8EEF7`.
  - Status pill: Microsoft YaHei UI, 8 px bold.
  - Detail: Microsoft YaHei UI, 8 px, `#B8C6D8`.
  - Progress: Consolas, 8 px, `#8492A6`.

## Status Colors

- Running: accent `#F3D27A`, pill background `#1C190F`.
- Waiting for user: accent `#FFB86B`, pill background `#1D1610`.
- Tool execution: accent `#9CCBFF`, pill background `#0D1722`.
- Just completed: accent `#8FE3A1`, pill background `#0E1B14`.

## Behavior

- Show one bubble per active Codex work item.
- Hide the overlay when there are no active items.
- Keep the overlay topmost and independent of Codex window visibility.
- Bubble text is compacted instead of scrolling; long detail text wraps inside the card.
- The main HUD remains unchanged: Renderer/Tk still display token and budget panels, while the activity overlay is desktop-level.
