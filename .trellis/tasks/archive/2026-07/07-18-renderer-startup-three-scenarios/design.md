# Technical Design

## Outcome And Invariants

Renderer startup is classified before connection recovery begins. Exactly one of
three scenarios owns the initial action:

```text
Codex desktop process present?
  no  -> Scenario 1: select a bindable port, launch once with CDP, attach
  yes -> verified Codex CDP target available?
           yes -> Scenario 3: attach to that exact port once
           no  -> Scenario 2: wait indefinitely for an explicit restart click
```

The classifier must not infer a scenario from a generic connection timeout. A
port is an existing-CDP match only after the current Codex desktop process and
the CDP endpoint both pass identity checks. Scenario 2 never stops Codex until
the user clicks. A recovery launch is carried into the next renderer session so
the window preparation path cannot launch a second instance.

## Startup Classification

`src/codex_usage_hud/cli.py` remains the startup owner. Introduce a small
scenario/plan value with the scenario, selected port, port source, and diagnostic
reason. Both daemon and direct HUD entry paths consume the same classification
rules.

| Input state | Plan | Required behavior |
|---|---|---|
| No verified Codex desktop process | `launch` | Pick a bindable port, launch Codex with CDP once, then wait and attach. |
| Codex desktop exists and a candidate exposes a verified Codex page target | `attach` | Set the discovered port, attach directly, and never show restart UI. |
| Codex desktop exists but no candidate exposes a verified Codex page target | `restart-required` | Show the persistent system action; do not allocate a future port or stop Codex yet. |
| Recovery session already launched Codex | `attach-launched` | Wait for that launch on its requested port with `launch_if_missing=False`. |

`_prepare_codex_window_for_renderer()` remains responsible for focus/visibility,
not startup classification. Any launch it still performs must be routed through
the same launch-port selector; duplicate launch call sites are removed or made
unreachable by the plan.

## Existing-CDP Port Discovery

Candidate discovery is bounded; there is no local port-range scan.

1. Read command lines only from processes accepted by the existing Codex desktop
   identity rules. `ChatGPT.exe`/desktop installation paths are accepted;
   npm/standalone `codex.exe` is rejected.
2. Extract both Chromium forms:
   `--remote-debugging-port=<port>` and `--remote-debugging-port <port>`.
3. Add configured and persisted candidates (`CODEX_USAGE_HUD_CDP_PORT`,
   `lastRequestedPort`, `lastSuccessfulPort`, and the default) without duplicates.
4. For each candidate, call the existing `list_targets()` and
   `pick_page_target()` boundary. Only a live `page` target identified as Codex
   (`app://`/Codex title) is attachable.
5. Select deterministically and log source, rejected candidates, and ambiguity.

Windows process command lines are obtained through one bounded CIM/PowerShell
query and parsed as JSON. macOS uses one bounded `ps` query and filters the app
executable/command against the configured or standard Codex app identity. Query
failure is non-fatal: known candidates are still validated, and if none can be
verified the existing app is classified as Scenario 2.

A launch port follows a different rule from an attach port. It must pass
`_localhost_cdp_port_available()` immediately before launch. If the explicit,
persisted, or default port is occupied by anything other than the verified
running Codex target, allocate one fresh local port before launching. Persist
`lastRequestedPort` only for the chosen launch; persist `lastSuccessfulPort`
only after renderer attachment succeeds.

On restart, stop the verified Codex desktop family first, then choose a currently
bindable fresh port and launch once. This avoids selecting a port that becomes
stale while the restart bubble is left open.

## PySide6 System Action

The existing work-overlay state file gains an optional top-level
`systemAction`, separate from ordinary `items`:

```json
{
  "items": [],
  "systemAction": {
    "id": "restart-codex-for-renderer",
    "kind": "restartCodex",
    "title": "Codex restart required",
    "message": "Save your work, then restart Codex with CDP enabled.",
    "label": "Restart Codex",
    "persistent": true
  }
}
```

The actual visible copy remains Chinese and concise. The JSON above documents
the contract, not literal UI copy.

`DesktopWorkOverlay` separates ordinary-item enablement from system-action
availability. `work_overlay_max_items=0` suppresses session bubbles but cannot
suppress the restart action. The action starts the existing PySide6 helper if
the runtime is available. It does not create a synthetic session ID, enter the
normal item limit, participate in card/completed transitions, or expose dismiss.

`work_overlay_qt.py` renders a fixed-width card using the existing theme,
topmost positioning, hover opacity, and native click-hotspot machinery. It has a
visible `Restart Codex` command area and no close hotspot. The hotspot emits one
append-only command:

```json
{
  "action": "restartCodex",
  "actionId": "restart-codex-for-renderer",
  "requestedAt": 0
}
```

The helper marks the action requested immediately so repeated clicks cannot
produce multiple restarts. The parent consumes only the first matching command.

## Persistence And Wakeup

The command path stays under the existing `FileChangeWatcher` contract. Startup
waiting uses an event set by the command-file watcher, plus a blocking helper
process-exit monitor; it does not add a 200 ms command poll.

A persistent `systemAction` ignores state-file age while its owner PID remains
alive. Owner exit, explicit `close=true`, or the consumed restart action closes
the helper. This makes "do nothing" an indefinite stable state without periodic
state rewrites. Ordinary work-item stale and keepalive behavior is unchanged.

The helper reports readiness through the same command channel. If PySide6 is
missing, the helper fails before readiness, or the process exits unexpectedly,
the parent records a structured fallback diagnostic and transfers the same
indefinite restart interaction to the existing lightweight startup card.

## Renderer Flow Integration

Scenario 2 is handled before constructing a renderer client that is known to
point at the wrong port. On click:

```text
restartCodex command
  -> close PySide6 action
  -> return HUD_SWITCH_TO_RENDERER_RESTART_CODEX
  -> stop verified desktop process family
  -> select fresh bindable port
  -> launch Codex once with CDP
  -> re-enter renderer session with launched_codex=True
  -> attach and remember successful port
```

Scenario 3 sets the verified discovered port before `RendererHudClient` is
constructed. If attachment races with target replacement, perform only bounded
same-port revalidation/retry. Show restart UI only if revalidation proves that
the Codex target is no longer available; a payload/script error on a still-valid
CDP target remains an explicit renderer error, not a false non-CDP classification.

## Diagnostics

Add concise structured stages to `renderer_fallback.log`:

- `renderer_startup_classified`: scenario, port, source, and reason.
- `renderer_cdp_process_port_discovered`: platform, verified desktop PID, port.
- `renderer_cdp_candidate_rejected`: port, source, bounded validation reason.
- `renderer_restart_overlay_fallback`: missing PySide6, readiness failure, or
  helper exit reason.
- `renderer_restart_requested_by_user`: action ID and the selected post-stop
  launch port.

Do not log full command lines or unrelated process arguments.

## CLI Compaction Continuation Boundary

Codex CLI context compaction writes a synthetic `final_answer` handoff before a
top-level `compacted` record and/or an `event_msg.context_compacted` marker. The
same task then continues without another `task_started` or `user_message`.

`JsonlSessionParser.latest_task_segment_start()` therefore treats both compaction
markers like the existing user-steer boundary for terminal-state lookup only.
`task_started_at`, the original prompt, task ordinal, and usage history continue
to belong to the same task. `task_completed_at`, `task_aborted_at`, and
`final_answer_at` may only come from records after the latest continuation
boundary. This keeps active-work rendering square during the resumed request and
still permits a later real terminal record to produce the completed circle.

## Compatibility And Rollback

- Renderer HUD remains the only product HUD. `qt_hud.py` and `tk_hud.py` are not
  modified.
- The default Windows package remains PySide6-free. The lightweight card is the
  emergency fallback only when the optional helper is unavailable.
- Windows receives real-process/live-CDP acceptance. macOS receives process-query,
  parsing, and flow tests plus existing CI smoke; no new real-device claim is made.
- Ordinary session bubbles retain their payload, item-limit, animations, clicks,
  keepalive, and theme behavior.
- Rollback is limited to startup selection/action state and their tests. The
  existing parser/work-status changes in the working tree are unrelated and must
  remain intact.
