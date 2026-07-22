# Safe Cleanup Contracts

## Scenario: Usage insights and consent-driven local cleanup

### 1. Scope / Trigger

- Trigger: renderer settings exposes local usage insights, regenerable cache
  cleanup, HUD diagnostic cleanup, or row-level SQLite history maintenance.
- Scope: `UsageSummaryCache` projections, renderer settings commands,
  `SafeCleanupManager`, the offline maintenance helper, process gates, and
  maintenance result recovery.
- Renderer mode is the only product surface. Qt/Tk must not receive this
  behavior.

### 2. Signatures

- `UsageSummaryCache.insights(...) -> dict[str, object]` reads already-parsed
  per-session contributions; it must not start another session-directory scan.
  Its session rankings recursively fold subagent contributions into the
  canonical root user thread and expose only the neutral `workdirName` leaf.
- `SafeCleanupManager.scan(request_id="") -> dict[str, object]` creates a new
  inventory revision and invalidates prior confirmations.
- `SafeCleanupManager.preview(item_ids, revision, *, consent=False,
  backup_directory=None, request_id="") -> dict[str, object]` issues a short-lived
  single-use confirmation token after validating the selection. Its operation
  payload includes `selectedIds`, `includesConsent`, `requiresBackup`,
  `estimatedBytes`, `backupBytes`, `sameVolumeBackupBytes`,
  `netEstimatedBytes`, `backupVolumeLabel`, and `backupDirectoryLabel`.
- `SafeCleanupManager.create_plan(item_ids, revision, confirmation_token, ...)
  -> MaintenancePlan` revalidates every selected object before creating an
  offline plan.
- `CacheDefinition.tier` is either `safe` or `consent`. Expiring definitions
  emit only top-level children whose complete trees are older than the cutoff;
  blocked or unverifiable children become `protected` regardless of the
  configured tier.
- Renderer commands use two distinct contracts:
  - `safeCleanupPreview` sends `groupIds`, `inventoryRevision`,
    `consentConfirmed`, and `backupDirectory`.
  - `safeCleanupExecute` sends the preview-bound `groupIds`,
    `inventoryRevision`, `confirmationToken`, and the current
    `autoCloseAndRestore` permission. It must not reinterpret resubmitted
    consent or backup-directory fields.
- Hidden helper command:

  ```text
  python -m codex_usage_hud --cleanup-maintenance-helper \
    --cleanup-plan-file <absolute-plan-path>
  ```

- SQLite targets:
  - `logs_2.sqlite`: delete recognized `logs` rows where `ts < now - 24 hours`.
  - `background-usage.sqlite3`: delete recognized audit history where its
    timestamp is older than 30 days; keep schema and scan state.

### 3. Contracts

- Renderer payloads contain opaque item IDs, neutral labels, categories,
  counts, bytes, retention, impact, process requirements, and aggregate usage.
  They do not contain cleanup source paths, prompts, responses, credentials, or
  raw database rows.
- `sessions`, `topSessionsByUsage`, and `topSessionsByCost` rank canonical root
  user threads, not rollout files. A subagent and any nested subagents contribute
  their post-fork usage exactly once to the root thread aggregate and never
  appear as separate rows. Provider/model totals continue to include that usage.
  If the referenced root rollout is unavailable, the aggregate uses the root ID
  but remains non-actionable instead of opening a child thread.
- Session ranking rows expose `workdirName` as the final directory component for
  display and ellipsis tooltips. They must not send the absolute `cwd` through
  the usage-insights payload.
- Background-usage `confirmed` / `pending` / `history` values remain internal
  reminder-lifecycle metadata. History rows and detail headers render the
  compact local `lastSeenAt` timestamp in the former status-badge position;
  they must not describe an already-recorded request as user-visible
  "processed" or "pending" work. The history row metadata line then contains
  the model label without repeating the timestamp.
- A SQLite preview reports cleanup bytes and backup bytes separately. Backup
  bytes on the source volume are subtracted from `netEstimatedBytes`; backup
  bytes on another volume are not. Renderer preview and second confirmation
  use the net source-volume estimate and explicitly identify cross-volume
  backup behavior. Before preview, the UI states that backup space will be
  calculated instead of rendering an uninitialized `0 B` estimate.
- Maintenance result projection exposes only aggregate `backupBytes`, backup
  filenames, a volume label, and the final backup-directory name. Absolute
  backup paths remain in the helper result and never enter the renderer result
  domain.
- `safe` items are selected by default. HUD current/rotated diagnostics are
  `safe`, require the HUD to exit, and do not require a SQLite backup.
- User-scoped Windows DirectX/vendor shader caches and macOS Homebrew/Xcode
  caches are `safe`. Old Windows/macOS crash and error reports use a seven-day
  cutoff and are `consent`; selecting them alone must not require a SQLite
  backup.
- `consent` items are never selected by default. Selecting either SQLite target
  requires separate consent, an absolute verified backup directory, and explicit
  approval to close and restore Codex App plus the HUD.
- A successful preview freezes its selected IDs, consent status, and canonical
  backup directory. Renderer consent/backup controls remain disabled until the
  preview is cancelled, and the confirmation text is derived from
  `operation.selectedIds` / `operation.includesConsent` plus the renderer's
  preview-time directory snapshot. The Python confirmation token remains the
  authority if renderer state drifts.
- Truncated preview/result lists order `consent` groups before `safe` groups so
  history-loss items remain visible even when many default cache groups are
  selected.
- `protected` items have no executable action. Unknown files, reparse points,
  active runtime data, config, credentials, sessions, `state_5.sqlite`,
  `goals_1.sqlite`, and `memories_1.sqlite` remain protected. Recycle Bin/Trash
  is not a path definition until a dedicated native action can preserve its
  current-user and recoverability contracts.
- Every path action is bound to one inventory revision, opaque ID, canonical
  approved root, `lstat` identity, content fingerprint, and lock/process gates.
  Current HUD logs may grow append-only between scan and helper execution;
  replacement, truncation, reparse, or identity change is rejected.
- Related system/browser/editor caches are rechecked both when creating the plan
  and in the helper. If a related process is running, the whole cache definition
  remains unchanged.
- The helper waits for the exact parent/target PIDs and database locks. It never
  force-kills an independent Codex CLI. Active tasks or any independent CLI
  make offline SQLite maintenance fail closed before process shutdown.
- Every SQLite action also re-enumerates Codex process names immediately before
  backup and again after backup, before opening the write transaction. An
  unavailable process inventory fails closed. A Codex process appearing at the
  second gate leaves the source unchanged and preserves the completed backup.
- SQLite modification begins only after `sqlite3.Connection.backup()` succeeds
  and the backup passes `PRAGMA integrity_check`. The source is checked before
  and after deletion; post-commit validation failure restores from that backup.
- `logs_2.sqlite` accepts only the `logs` table plus the optional known
  `_sqlx_migrations` metadata table. `logs` must expose exactly the current 12
  columns (including `level`, `file`, `line`, and `estimated_bytes`), no foreign
  keys, and exactly the four known indexes with their expected column order and
  partial-index flag. Migration metadata columns must match exactly and contain
  no unsuccessful migration. Any non-internal trigger/view, extra column,
  unknown/missing index, or other application table protects the whole database.
- Plan/result files are versioned, digest-protected, atomically written, bounded
  in size, and constrained to the runtime plan directory. The helper consumes
  the plan, persists an explicit result, and restores only validated absolute
  restart commands.
- No page, session, config, filesystem, or explicit command event means no
  recurring insights or cleanup scan.

### 3b. Progressive scan feedback

- While `SafeCleanupManager.scan` runs, the worker may publish partial snapshots through
  `progress_publisher` with `operation.state = "scanning"`.
- Scanning partials use a temporary revision prefix `scanning:` and must never mint a
  confirmation token or accept `preview` / `execute` against that revision.
- Progressive operation fields (path-neutral only):

  - `phase`, `phaseLabel`, `phaseIndex`, `phaseCount`
  - `discoveredGroups`, `discoveredBytes`
  - `progress` stays in `0..99` until the final completed/preview snapshot

- Renderer shows boot shell / scan strip / progressive rows from these fields.
  Confirm cleanup remains disabled until a real inventory revision reaches
  `operation.state = "preview"` with a confirmation token.
- Session cleanup may publish the same phase progress fields during scan without
  partial session rows.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Inventory revision or confirmation token is stale | Reject before creating a plan. |
| A rollout is a direct or nested subagent | Merge its live usage into the canonical root session; do not emit a child ranking row. |
| A subagent references a root rollout that is unavailable | Keep one non-actionable root-ID aggregate; do not fall back to opening the child. |
| A background event carries any reminder acknowledgement state | Render `lastSeenAt` in the row/detail header and keep acknowledgement labels out of the history UI. |
| Renderer selects a protected or unknown opaque ID | Reject the entire selection. |
| Consent item is selected without separate consent | Reject preview. |
| SQLite is selected without a valid backup directory | Reject preview/plan; do not create or modify the source. |
| Consent or backup controls change after preview | Keep controls locked; confirm and execute only the token-bound preview or require a new preview. |
| Backup space, backup API, or backup integrity check fails | Mark the item failed and leave the source unchanged. |
| Backup directory is on the source volume | Subtract its estimated bytes from source-volume net reclaim and show the same-volume cost. |
| Backup directory is on another volume | Keep source-volume net reclaim equal to cleanup bytes and state that backup does not consume the source volume. |
| SQLite table, exact columns, migration state, indexes, foreign keys, trigger, or view is unknown | Publish a protected group; do not issue a SQLite action. |
| Source fingerprint/schema changes after planning | Helper skips/fails the item without mutation. |
| Current task/background work cannot be proven idle | Do not close any process and do not modify data. |
| Independent Codex CLI exists | Do not terminate it and do not run offline SQLite maintenance. |
| Codex appears before backup | Fail the SQLite action before creating a backup or modifying the source. |
| Codex appears after backup | Preserve the backup, fail before `BEGIN EXCLUSIVE`, and leave the source unchanged. |
| Related cache application starts after scan | Helper recheck fails; cache remains unchanged. |
| Post-delete integrity or critical query fails | Close connections and restore the source from the preserved backup. |
| Restart fails after cleanup | Keep the cleanup result and report restart failure separately. |

### 5. Good/Base/Bad Cases

- Good: the default preview selects regenerable caches and HUD diagnostics,
  shows the HUD restart requirement, and leaves both SQLite groups unchecked.
- Good: a root session with child and grandchild agents appears once in Top10;
  its token/cost totals include all three live contributions and its row shows
  only the root workdir leaf.
- Good: a background history row shows `07/21 18:05` beside its title and the
  model on the next line, regardless of whether its reminder was dismissed.
- Base: a child references a missing parent rollout; usage remains visible under
  the parent ID, but the row has no session-open action.
- Bad: rank each subagent JSONL independently, omit its usage entirely, or send
  an absolute session `cwd` merely to render the left-side directory label.
- Bad: label background history as "pending" or "processed"; those words imply
  request execution state even though the request and usage are already final.
- Good: the user opts into old Codex diagnostics, sees the 24-hour retention and
  mandatory backup, then an idle helper backs up, deletes only old `logs` rows,
  verifies integrity, and restores the previous launch shape.
- Good: the current `logs_2.sqlite` includes successful SQLx migration metadata
  and the current `estimated_bytes` column; it is recognized without weakening
  the unknown-table gate.
- Good: a preview includes SQLite history, then its controls stay locked and the
  second confirmation still describes the same history and backup directory.
- Good: a C: SQLite fixture backed up to E: reports zero same-volume backup
  bytes, keeps net reclaim equal to cleanup bytes, and the result shows only
  `E:`, the directory tail, backup size, and backup filename.
- Base: a relevant app is running, no history exceeds retention, or backup space
  is insufficient; the affected group is protected/skipped and other inventory
  remains usable.
- Bad: raw-delete a SQLite/WAL/SHM file, expose absolute source paths to the
  renderer, describe cleanup bytes as net reclaim while a same-volume backup is
  required, treat user consent as an override for active tasks, accept arbitrary
  extra schema objects, rebuild consent text from mutable controls, or force-kill
  an independent CLI and claim it was restored.

### 6. Tests Required

- `tests/test_safe_cleanup.py`:
  - fixed HUD log whitelist and protected runtime files;
  - Windows/macOS shader/developer cache definitions, seven-day diagnostic
    consent, non-SQLite backup independence, and Recycle Bin/Trash exclusion;
  - opaque payloads, revision/token expiry, path escape, reparse, fingerprint,
    lock, process-start, and append-only log growth checks;
  - real `logs_2.sqlite` table/index fixture including `_sqlx_migrations`, 24-hour
    deletion, backup integrity, VACUUM, failure restoration, unknown table,
    trigger/view, extra column/index, index-shape, and failed migration rejection;
  - Codex process detection before backup and after backup/before mutation, with
    source-row and preserved-backup assertions;
  - background audit 30-day deletion while preserving schema and scan state;
  - plan/result digest, path boundary, PID wait, and restart command validation.
  - same-volume versus cross-volume preview accounting and result projection
    that strips absolute backup paths while retaining neutral location labels.
- `tests/test_ui.py`: active-task, independent CLI, background runtime shutdown,
  Codex close, helper launch, daemon exit code, recovery result flow, and native
  backup-directory selection persistence with request correlation; usage
  insights also cover recursive subagent-to-root aggregation, unchanged overall
  totals, root actionability, and workdir-leaf privacy.
- `tests/test_renderer_hud.py`: domain-only updates, immutable preview-bound
  consent/backup controls and confirmation copy, readable group/result labels,
  net-reclaim/cross-volume copy, consent-first truncated preview ordering,
  execute-command field boundaries, no opaque IDs as visible copy,
  background history timestamps replacing reminder acknowledgement labels,
  and space-cleanup visual structure contracts (compact segments, 572px dialog,
  empty-state, summary band / 54px rows, six-column session head, danger
  confirm).
- Live Windows acceptance: open Space Cleanup in the real renderer, scan and
  preview only, verify all three tiers, HUD logs default-selected, SQLite
  unchecked with 24-hour retention, no horizontal overflow, and no deletion.
- macOS remains covered by platform adapter contract tests plus a manual smoke
  checklist when a machine is available.

### 7. Wrong vs Correct

#### Wrong

```python
# Consent, a path string, and a DELETE are not sufficient authority.
sqlite3.connect(source).execute("DELETE FROM logs WHERE ts < ?", (cutoff,))
```

```python
# Rollout files are not user-visible session identities.
ranked_sessions = [project(entry) for entry in cache_entries]
```

```javascript
// Cleanup bytes are not net source-drive reclaim when backup stays on that drive.
confirm(`预计释放 ${operation.estimatedBytes}`);
```

#### Correct

```python
inventory = manager.scan()
preview = manager.preview(
    selected_ids,
    inventory["revision"],
    consent=True,
    backup_directory=verified_backup_root,
)
plan = manager.create_plan(
    selected_ids,
    inventory["revision"],
    preview["operation"]["confirmationToken"],
)
# The offline helper rechecks PIDs, paths, locks, schema, backup, and integrity.
result = run_maintenance_plan(plan)
```

```python
# Resolve parent_thread_id transitively, then merge each contribution once.
root_id = resolve_root_session(entry, entries_by_session_id)
merge_usage(session_totals[root_id], entry.window_usage)
```

```javascript
confirm(`预计源盘净释放 ${operation.netEstimatedBytes}`);
```

The renderer owns user intent and readable summaries. Python inventory owns
paths and authority. Only the offline helper owns mutation, after every gate is
revalidated.

## Scenario: Renderer-managed permanent session deletion

### 1. Scope / Trigger

- Trigger: the renderer Session Management section scans local Codex threads,
  previews a selected root-session batch, or permanently deletes that batch.
- Scope: Windows CLI wrapper resolution, state/index/rollout inventory, spawn
  tree grouping, renderer selection state, one-shot confirmation, official
  delete execution, and post-command verification.
- Permanent deletion is independent from garbage cleanup. It never creates a
  path-deletion or direct SQLite fallback.

### 2. Signatures

- `SessionCleanupManager.probe_capability() -> SessionDeleteCapability` runs
  `codex delete --help` and requires a successful result that advertises
  `--force`.
- `SessionCleanupManager.scan(request_id="") -> dict[str, object]` creates a
  root-session inventory revision and invalidates all earlier confirmations.
- `SessionCleanupManager.preview(item_ids, revision, request_id="") -> dict`
  accepts selectable opaque IDs and issues a short-lived, single-use token.
- `SessionCleanupManager.execute(item_ids, revision, confirmation_token,
  request_id="") -> dict` serially invokes the official command and verifies
  each selected family after the command returns.
- The only mutation command is:

  ```text
  codex delete --force <canonical-root-UUID>
  ```

- Renderer commands are `sessionCleanupScan`, `sessionCleanupPreview`,
  `sessionCleanupExecute`, and `sessionCleanupCancel`. UUIDs and absolute paths
  do not cross this command boundary.

### 3. Contracts

- Inventory reads `threads` and `thread_spawn_edges` from `state_5.sqlite` in
  read-only mode, plus the session index and active/archived rollout mapping.
  Only canonical root sessions are rows; direct and nested descendants are
  represented by `descendantCount`.
- Current sessions, running roots, roots with active descendants, ambiguous
  spawn graphs, non-canonical IDs, and unprovable rollout mappings remain
  protected. A renderer selection cannot override those gates.
- On Windows, the default runner first resolves `codex` against the supplied
  `PATH` with `shutil.which()`. This allows npm's `codex.CMD` wrapper to run
  while retaining an argument-vector call with `shell=False`. The resolved
  absolute wrapper path stays Python-side and is not exposed in the payload.
- The official CLI owns the spawn-subtree deletion. The manager calls it once
  for the selected root, then requires the root and every recorded descendant
  to be absent from the state DB, session index, and active/archived rollout
  paths. A zero exit code without this evidence is a failure.
- Scan, preview, and execute use their own revision, opaque IDs, and one-shot
  token. Batch execution is serial; a failed item does not erase the success or
  retryability of other items.
- Renderer selection authority is limited to the visible filter context.
  Changing search text, status, or time clears `selectedIds` immediately before
  repaint, so a hidden selection cannot leave the permanent-delete action
  enabled. Cancelling a preview does not broaden selection authority.
- Payloads may contain neutral title, workdir leaf, timestamps, status, bytes,
  and descendant counts. They never contain UUIDs, absolute rollout paths,
  prompts, or responses.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| `codex` resolves only to an npm `.CMD` wrapper | Resolve it through the supplied `PATH`, keep `shell=False`, and probe normally. |
| `delete --help` fails or omits `--force` | Keep the inventory read-only and publish the capability reason. |
| A root or descendant is current/running | Protect the whole root row and reject forged selection. |
| Spawn ancestry is cyclic, ambiguous, or changes after scan | Protect or reject the family without invoking the CLI. |
| Search, status, or time filter changes | Clear all selected opaque IDs before repaint and disable delete. |
| Revision, selected IDs, or token differ from preview | Reject before invoking the CLI and consume no unrelated token. |
| CLI exits zero but DB, index, or rollout evidence remains | Mark that item failed; never raw-delete the residue. |
| One item in a batch fails | Return `partial`, preserve per-item results, and leave failed items retryable after rescan. |

### 5. Good/Base/Bad Cases

- Good: npm installs only `codex.CMD`; capability probing resolves that exact
  wrapper without a shell, and a confirmed root delete is verified across its
  complete descendant family.
- Good: one selectable row is checked, then the user changes to the
  Current/Running filter; the selected count becomes zero and Permanent Delete
  is disabled even though the old row is no longer visible.
- Base: the installed CLI has no non-interactive delete command; users can
  inspect the grouped inventory but cannot select or execute rows.
- Bad: use `shell=True`, pass a renderer-supplied UUID to the CLI, invoke delete
  once per child, retain hidden selections across filters, or treat exit code
  zero as proof that deletion completed.

### 6. Tests Required

- `tests/test_session_cleanup.py`: root/descendant grouping and payload privacy;
  current/running/ambiguous graph protection; `.CMD` resolution through the
  supplied `PATH`; force-only capability; revision/token single use; official
  root command; DB/index/rollout family verification; and partial batch results.
- `tests/test_renderer_hud.py`: contract assertions require each search/status/
  time assignment to be immediately followed by `selectedIds.clear()`.
- Isolated end-to-end gate: use a temporary `CODEX_HOME` with active, archived,
  parent, and nested-child fixtures; run the installed CLI and verify state DB,
  index, and rollouts. Never use a real user session for this gate.
- Live renderer gate: select one real but protected-by-nonexecution row, change
  each filter, and assert selected count zero, delete disabled, no horizontal
  overflow, and no console/window errors. Do not confirm execution.

### 7. Wrong vs Correct

#### Wrong

```python
# A shell broadens parsing/quoting behavior and still does not prove deletion.
subprocess.run("codex delete --force " + session_id, shell=True)
```

```javascript
// The row disappears, but its opaque ID can still authorize the danger action.
sessionCleanupState.status = nextStatus;
renderSettingsModal("storage");
```

#### Correct

```python
resolved = shutil.which(command[0], path=environment.get("PATH"))
argv = [resolved or command[0], *command[1:]]
subprocess.run(argv, shell=False, env=dict(environment), check=False)
# Then verify the root and recorded descendants in DB, index, and rollouts.
```

```javascript
sessionCleanupState.status = nextStatus;
sessionCleanupState.selectedIds.clear();
renderSettingsModal("storage");
```

The official CLI owns deletion semantics. The HUD owns bounded intent,
pre-command safety gates, and independent post-command evidence.

## Scenario: Space cleanup renderer layout contracts

### 1. Scope / Trigger

- Trigger: the settings modal `storage` tab is remodeled as Space Cleanup with
  junk cleanup and session management sections.
- Scope: renderer-only CSS/markup and visual structure tests. Backend inventory,
  preview/execute tokens, and official delete gates remain unchanged.

### 2. Signatures

- Primary navigation label is Space Cleanup; in-page sections are only
  `data-cleanup-section="junk"` and `data-cleanup-section="sessions"`.
- Default junk flow has two primary actions: scan, then confirm cleanup.
  Scan auto-builds a safe-only preview/token; deep SQLite remains consent-gated.
- Session filters use compact chips (`data-action="session-cleanup-status"` /
  `session-cleanup-time`) instead of native selects. Select-all lives in the
  table header, not as a separate toolbar button.

### 3. Contracts

- Storage-tab dialog height is `min(572px, calc(100vh - 48px))`. Overlay bilateral
  padding is fully subtracted (`24px * 2 = 48px`) in width/height calc.
- Generic settings footer is hidden on the storage tab. Cleanup owns a dedicated
  about-`50px` action bar (`.codex-usage-hud-cleanup-footer`).
- Segmented control is left-compact: `minmax(96px, 1fr)` segments and about
  `31px` height (`.codex-usage-hud-cleanup-segments`).
- Pre-scan empty state is vertically centered: 62px scan mark, 17px title,
  12px meta, large primary button.
- Scan results expose a green summary band, stable about-`54px` category rows,
  yellow deep-clean rows (`data-kind="deep"`), and a compact protected summary.
- Desktop session table has a six-column head with named grid areas
  `check title workdir time status size`. 520–759px uses condensed grid areas;
  below 520px the head may hide and cwd/time move to secondary spans.
- Permanent-delete confirm is scoped with `data-tone="danger"`: danger mark,
  three-item summary, yellow note, and dedicated actions. Button tones for
  cleanup workspace stay local (primary/danger) and must not restyle other tabs.
- Lists scroll inside the content pane. Overlay, dialog, and workspace forbid
  horizontal overflow (`scrollWidth == clientWidth` on live acceptance).

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Storage tab opens before any scan | Show centered empty state and only the scan primary action. |
| Search/status/time filter changes | Clear session `selectedIds` before repaint and disable permanent delete. |
| Window narrower than design desktop | Keep six semantic columns via grid areas or secondary rows; no horizontal overflow. |
| Permanent delete confirm opens | Use danger-scoped card/actions; never reuse generic non-danger footer alone. |

### 5. Good/Base/Bad Cases

- Good: desktop shows six session columns, chips filter without leftover
  selection, and junk confirm sits in the dedicated footer.
- Base: mid-width stacks workdir/time while keeping checkbox and size stable.
- Bad: stretch the segmented control full width, put core actions only in the
  generic close footer, or keep native selects with a separate “select all
  filtered” toolbar button as the primary selection model.

### 6. Tests Required

- `tests/test_renderer_hud.py` asserts compact segments, 572px height, empty
  state, summary band / 54px rows / deep row, six-column grid areas, filter
  chips with clear-on-change, and danger confirm structure.
- Live renderer gate still requires no horizontal overflow and no
  console/window errors; do not execute real cleanup or real session deletion
  for visual acceptance.

### 7. Wrong vs Correct

#### Wrong

```javascript
// Generic settings footer owns cleanup actions; dialog stays 720px tall.
dialog.style.height = "720px";
```

#### Correct

```css
.codex-usage-hud-settings-dialog[data-active-tab="storage"] {
  height: min(572px, calc(100vh - 48px));
}
.codex-usage-hud-cleanup-footer {
  min-height: 50px;
}
```

Visual acceptance is design-board aligned structure plus live overflow/error
checks. Unit tests alone do not close the visual gate.
