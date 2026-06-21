# Top HUD Expanded Redesign Notes

This design artifact explores a simplified expanded top HUD that keeps the
current Codex App theme token model intact. It does not change application
code.

## Content Model

- Session usage becomes the primary left area: total cost, total tokens,
  confirmed rounds, cache hit rate, and current request estimate.
- Budget shows only actionable progress: today and this week used plus
  remaining budget. The visual progress rail keeps the existing HUD pill
  style.
- Status and symbol legend are removed from the default layout.
- Reminders are hidden by default and appear only as a warning strip when a
  budget threshold or parser/backend error is active.
- Realtime data is merged into Current Activity: active phase, elapsed time,
  wait time, current tool, and recent activity trail.
- Narrow states keep the same progress rail component and only reduce density:
  at medium width the layout becomes a single-column stack; at compact width
  token breakdown and activity history collapse into summary rows.

## Theme Contract

The mock uses the existing HUD token families:

- `surface`, `panelSurface`, `headerSurface`, `panelBorder`, `divider`
- `text`, `muted`, `accent`, `info`, `warning`, `error`, `success`
- progress tones for session, day, week, cache, and overflow
