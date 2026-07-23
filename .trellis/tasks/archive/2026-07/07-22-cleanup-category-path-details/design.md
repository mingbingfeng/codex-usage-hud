# Cleanup categories, path details, and reveal action - Design

## Problem

The current cleanup domain uses one `CleanupItem` for both execution authority and presentation. That is correct for deletion safety but wrong for UI: every `%TEMP%` child becomes an identical visible row, the renderer truncates visible safe rows, and all safe item IDs still enter preview. The design must separate presentation grouping from execution granularity without weakening revision/path/fingerprint checks.

## Scope

This is one integrated deliverable rather than a parent/child task tree. Payload metadata, renderer grouping, selection authority, reveal commands, and result projection share the same item IDs and revision contract; splitting them would create an intermediate state where paths or selections are visible but not trustworthy.

In scope:

- per-item local path metadata in safe-cleanup payloads;
- renderer-side grouping and group selection;
- expandable exact-path details with copy/reveal actions;
- Windows Explorer and macOS Finder reveal adapters;
- grouped preview/execution presentation;
- privacy/spec/test updates; optional user-facing manual check notes (not a hard gate).

Out of scope:

- Qt/Tk behavior;
- deleting arbitrary user-selected paths;
- scanning file contents to infer application ownership;
- changing cache whitelist, retention periods, or deletion helper semantics;
- Recycle Bin/Trash support.

## Product Shape

Visual thesis: a quiet filesystem inspector inside the existing cleanup workspace - compact category rows for scanning, exact monospace paths only on demand, and restrained status/action color.

Content plan:

1. Existing reclaim summary band.
2. One row per cleanup rule/tier/risk boundary with source summary, target count, file count, and bytes.
3. Inline detail list containing exact paths and per-target metadata/actions.
4. Existing dedicated cleanup footer for the two-step scan -> confirm flow.

Interaction thesis:

- Chevron expands/collapses details without changing selection.
- Category checkbox selects/deselects every executable child and regenerates the preview for that exact ID set.
- Copy gives local transient feedback; reveal submits an opaque ID and reports success/error through the existing settings status surface.

## Data Model

`CleanupItem` remains the execution unit. Its local renderer payload gains:

```text
path: absolute local path or ""
pathKind: "file" | "directory" | "unknown"
modifiedAt: ISO timestamp or ""
```

Existing `id`, `category`, `tier`, `bytes`, `files`, `retention`, `impact`, requirements, and blocked reason remain unchanged. The Python manager still keeps canonical `Path`, approved root, lstat, and fingerprint as private authority.

The renderer constructs a presentation group from raw items with an exact grouping tuple:

```text
category, tier, label, retention, impact, blockedReason,
requiresOffline, requiresBackup, requiresCodexClose
```

Each group contains `itemIds` and `entries`, plus summed bytes/files/target count and the time range. Grouping never crosses tier, blocked reason, retention, or process requirements. The group ID is presentation-only and is never accepted by Python as deletion authority.

## Selection Contract

`safeCleanupState` gains revision-bound `selectedIds` and `expandedGroupIds` sets.

- A new final inventory revision initializes selection from `defaultSelectedIds`.
- A preview/result payload with the same revision preserves the user's selection.
- Toggling a category adds/removes all executable child IDs, hides the old preview, and requests a new preview.
- Preview/execute commands continue sending only raw item IDs in `groupIds` for compatibility with the existing Python API.
- The renderer may summarize those raw IDs by presentation group, but every selected child remains traceable in the expanded group.
- The `slice(0, 8)` truncation is removed for selected cleanup targets. Scrolling remains inside the content pane.

## Path Visibility

Absolute paths are allowed in the local safe-cleanup renderer domain. They may be displayed and copied, but must not be sent to external services or written into unrelated logs. `docs/PRIVACY.md` and the safe-cleanup spec will explicitly distinguish local path transparency from command authority.

The collapsed row shows a compact source summary such as `%TEMP%` or a path tail. The expanded list shows the full absolute path with wrapping and a title tooltip. Missing path metadata degrades to a non-actionable “path unavailable” detail instead of inventing a path.

## Reveal Boundary

Renderer command:

```json
{
  "action": "safeCleanupReveal",
  "requestId": "...",
  "inventoryRevision": "...",
  "itemId": "opaque-item-id"
}
```

No path field is accepted.

Flow:

```text
Renderer item action
  -> settings command bridge
  -> SafeCleanupManager.resolve_reveal_path(itemId, revision)
  -> current inventory lookup + absolute/approved-root/existence/reparse checks
  -> platform reveal adapter (shell=False)
  -> settings status response
```

Reveal is read-only and may be used for safe, consent, or protected items only when the stored path itself remains verifiable. It does not run deletion fingerprint/lock/process gates because it performs no mutation. Unknown IDs, stale/scanning revisions, missing paths, escaped boundaries, disappeared targets, and reparse targets are rejected before process launch.

Platform argv:

- Windows directory: `explorer.exe <directory>`
- Windows file: `explorer.exe /select, <file>`
- macOS directory: `open <directory>`
- macOS file: `open -R <file>`

The launcher receives an argument vector, `shell=False`, and detached/no-console flags appropriate to the platform. Tests inject the launcher and assert argv rather than opening real windows.

## Result Projection

Preview, active execution, and completed results map raw result IDs back to the same presentation groups. Group state is derived with failure precedence (`failed`, `skipped/partial`, `running`, `completed/deleted`). Bytes and deleted rows are summed. Expanded details preserve per-item state/error so a partial group remains auditable.

## Compatibility

- Payload additions are backward-compatible for older renderers.
- New renderer code tolerates payloads without path metadata.
- Existing preview tokens, maintenance plans, offline helper, backups, and revalidation stay item-based and unchanged.
- Renderer-only direction and event-driven update behavior remain intact; reveal runs only on explicit click.

## Risk And Rollback

- Main risk: selected IDs drifting when a new preview payload arrives. Bind selection to inventory revision and test scan -> toggle -> preview -> execute command payloads.
- Main security risk: arbitrary path launch. The command schema has no path, and manager resolution is the only source of the launched path.
- Main visual risk: long Windows paths causing overflow. Use a constrained detail grid, `overflow-wrap:anywhere`, stable icon buttons, and structural/CSS tests for narrow widths; optional user live checks at 760px/520px are recommended notes only.
- Rollback is additive: remove the reveal command/payload fields and restore raw group rendering; maintenance-plan data and on-disk formats are not migrated.
