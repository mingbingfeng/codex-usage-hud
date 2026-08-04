# HUD Runtime Decomposition Roadmap

Status: P0-P11 plus P7.2-P7.7 structural/automated gates complete; live platform acceptance outstanding
Last revised: 2026-08-03

## Decision And Current Status

The event-driven renderer refactor and the giant-file decomposition are separate
tracks.

- The earlier event-driven phases established the product direction, active-session
  authority, partial payloads, incremental usage processing, watcher-based overlay
  wakeups, and runtime diagnostics.
- The current working tree has completed the P0-P11 ownership moves, the P7.1
  wait-owner cutover, P7.2-P7.7 renderer/overlay owner slices, the P9
  runtime/overlay/activity owner slices, the P10 asset/activity owner slices,
  the P11 event/overlay/request owners, compatibility migration, package checks,
  and automated gates described below.
- Overall product acceptance remains open until real Windows Codex App and
  available macOS Renderer evidence is captured. A short compatibility facade by
  itself is not evidence that its former implementation has been decomposed.

This file is the only execution roadmap for the remaining decomposition. The old
completed phase list described behavioral work and is superseded by P0-P11 below.

## Document Roles

Use the documents in this order:

1. `docs/RENDERER_MODE_STRATEGY.md` defines normative renderer product and runtime
   constraints.
2. This roadmap defines terminal ownership, dependency direction, migration order,
   and phase exit gates.
3. `docs/HUD_RUNTIME_ACCEPTANCE_CHECKLIST.md` defines recurring automated and manual
   checks.
4. `docs/HUD_RUNTIME_COMPLETION_AUDIT.md` records what has actually been proven.
5. `docs/HUD_RUNTIME_LIVE_VERIFICATION.md` is the real Codex App runbook.

If an older audit says a historical phase is done, that does not waive a gate in
this roadmap. Evidence must be regenerated after the relevant code changes.

## Current Checkpoint

The 2026-08-03 working tree contains the P0-P11 decomposition and remains
uncommitted:

| Surface | Current state | Terminal intent |
|---|---:|---|
| `cli.py` | 25-line explicit facade with four public bindings | Small explicit public facade; no implementation ownership |
| `runtime_orchestration.py` | 352 lines | Composition root and compatibility exports, at most 400 lines |
| `renderer_runtime.py` | 409 lines | Session orchestration root with startup/attach, loop wiring, and final shutdown |
| `renderer_runtime_assembly.py` | 263 lines | One-shot context, resource, bridge, client, and session-adapter assembly |
| `renderer_session_ports.py` | 106 lines | Frozen data-only renderer session composition contract |
| `renderer_runtime_policies.py` | 349 lines | Pure renderer refresh/invalidation policy owner |
| `renderer_session_lifecycle.py` | 260 lines | Resource shutdown, loop controls, and startup feedback owner |
| `renderer_pre_refresh.py` | 191 lines | Command/background/settings pre-refresh owner |
| `renderer_event_loop.py` | 716 lines | Typed event sampling and refresh execution owner, with reduction compatibility re-exports |
| `renderer_event_reduction.py` | 166 lines | Pure event-to-refresh-plan reduction and event-batch coalescing |
| `renderer_event_normalization.py` | 131 lines | Pure sampled-event normalization owner |
| `renderer_wait.py` | 150 lines | Pure deadline/wait planning owner with typed wait ports |
| `overlay_window.py` | 104 lines | Detached overlay Codex window/refocus action owner |
| `overlay_transition_audit.py` | 97 lines | Overlay transition projection and v1 JSONL owner |
| `overlay_supervision.py` | 187 lines | Pure helper health, availability, backoff, keep-alive, and command policy |
| `desktop_overlay.py` | 816 lines | Helper/UI side effects, state publication, and revision/order owner |
| `overlay_command_channel.py` | 119 lines | Incremental command JSONL tail and acknowledgement sidecar owner |
| `overlay_state.py` | 82 lines | Overlay state signature, envelope, and writer contract owner |
| `renderer_activity_projection.py` | 488 lines | Pure top-level task/activity projection owner; trail moved to `renderer_activity_trail.py` |
| `renderer_activity_trail.py` | 202 lines | Pure activity event filtering, deduplication, and trail projection owner |
| `renderer_payload_builder.py` | 1441 lines | Final payload envelope and compatibility wrappers |
| `renderer_request_projection.py` | 380 lines | Pure request/task-round projection and row details |
| `renderer_assets/layout.py` | 14 lines | Single manifest-level layout asset assembly |
| `renderer_assets/layout_style.py` | 4383 lines | Static layout CSS/style bootstrap fragment |
| `renderer_assets/layout_markup.py` | 214 lines | Static layout markup helpers |
| `renderer_assets/layout_gestures.py` | 139 lines | Pointer gesture helpers |
| `renderer_assets/layout_anchors.py` | 470 lines | Geometry and anchoring helpers |
| `renderer_assets/layout_observers.py` | 397 lines | Resize/mutation observers and position sync |
| `renderer_assets/settings_support_panels.py` | 113 lines | Static settings support/about panel fragment |
| `renderer_assets/settings_shell.py` | 1514 lines | Settings shell prefix/suffix and domain assembly |
| `ui/renderer_hud.py` | compatibility alias | Small explicit renderer facade |
| `ui/renderer_domains.py` | 184-line explicit compatibility facade | Documented renderer exports only; no dynamic attribute fallback |
| `ui/renderer_script.py` | 11-line bundle facade | Explicit JS/CSS domain assets |
| `renderer_cdp/__init__.py` | 6-line transport facade | `renderer_cdp` package owns transport-only modules |
| `overlay_ipc.py` | contract module with compatibility exports | Versioned contract-only module; no helper lifecycle or runtime coordination |
| `tests/test_ui.py` | 19,403 lines | Owner-based runtime/domain test modules |
| `tests/test_renderer_hud.py` | about 3,223 lines | Bundle, payload, DOM behavior, client, and transport test modules |

The current `runtime_policies.py`, `runtime_settings.py`, `runtime_usage.py`,
`overlay_ipc.py`, `renderer_cdp.py`, `ui/renderer_domains.py`, and
`ui/renderer_script.py` are migration seeds. Their existence does not freeze them as
terminal modules, and their contents must still obey the final dependency graph.

No commit should be made solely because a phase is described here. Commit only when
explicitly requested.

Current automated evidence is recorded in the P6.7/P7.1-P7.7/P9/P10/P11 phase
logs and the 2026-08-03 completion-audit checkpoint. The P7.2-P7.7, P9, P10,
and P11 owner slices are implemented and focused tests pass. The current
post-P11 wheel and PyInstaller onefile have been rebuilt and smoke-tested:

- wheel: `tmp/final-wheel-20260803-p11/codex_usage_hud-1.0.5-py3-none-any.whl`
  (SHA-256 `5e3b0c5a6288ae4ed250298bfdb935d13ba95c3125364bc04b7f7ee89bcde024`)
- onefile: `tmp/final-pyinstaller-20260803-p11/dist/codex-hud-p11.exe`
  (SHA-256 `8f4afebb624c3f32dc20828cac54449fe39fd50ac477858b9d5f7727a4e5562c`)
- fresh-venv wheel import, recursive archive inspection, and onefile `--help`
  all pass; details are recorded in `docs/HUD_RUNTIME_COMPLETION_AUDIT.md`.

The later canonical-id selection-identity fix supersedes that historical P11
package for current source verification. Its fresh-venv wheel and PyInstaller
onefile smoke are recorded in the handoff; the current template is `624498`
bytes and the empty-catalog bundle is `624470` bytes. This fix keeps payload
updates for same-thread title/raw-ID changes while preserving the existing
sequence boundaries for distinct or provisional selections. It has not been
verified in a post-restart real Codex App runtime.

Real Windows startup, settings, DEBUG, thread
selection, root replacement, and remove/reinject evidence is captured; the
active-session timing target remains an observed fail and no new request was
submitted. The remaining acceptance gap is current-request latency, a valid
no-event idle CPU sample, native gesture coverage, and available macOS
package/startup/Renderer evidence. The remaining renderer wiring should be
split only with another concrete owner boundary, not by moving mixed policy and
side effects into an unrelated module. P7.1-P7.7 are the accepted pure
boundaries below the event loop/session assembly root.

## Scope

This roadmap decomposes:

- renderer payload and presentation domains;
- the injected renderer script and styles;
- runtime startup, event reduction, refresh execution, and shutdown;
- usage cache, insights, cleanup transactions, and snapshot construction;
- detached desktop-overlay projection, IPC, supervision, and commands;
- CDP target, connection, and binding transport;
- daemon and CLI entry points;
- tests that currently depend on giant implementation modules.

## Non-Goals

Do not combine this refactor with:

- a visible HUD redesign or copy change;
- new Qt/Tk product behavior or a Qt/Tk fallback;
- app-server active-session authority;
- merging the existing CDP bindings into a new transport protocol;
- changing websocket reuse, reconnect, or launch policy while moving code;
- changing debounce, stale timeout, retry, or reminder timing merely because code
  moves;
- broad performance rewrites without a separately measured regression;
- public support for tests monkeypatching private module globals.

PySide remains an optional detached-helper dependency only. Overlay extraction may
repair helper correctness and test isolation, but it must not turn the helper into a
second main HUD.

## Non-Negotiable Invariants

### Product And Platform

- Renderer mode is the canonical and only main HUD product surface.
- Windows and macOS remain first-class targets.
- Visible layout, copy, storage keys, command behavior, and current public CLI
  behavior stay unchanged during mechanical decomposition.

### Event-Driven Runtime

The invariant remains:

```text
no relevant event -> no snapshot rebuild, no all-session scan, no CDP payload push
```

Budget boundaries, keepalive deadlines, bounded retries, and reminder deadlines are
explicit scheduled events. They are not permission for an unconditional work loop.

### Ownership And Dependencies

- Every production symbol has exactly one implementation owner.
- Compatibility modules re-export an explicit allowlist; they contain no duplicated
  implementation.
- Domain modules never import `cli`, `runtime_orchestration`, `renderer_domains`, or
  `renderer_hud`.
- Transport modules know envelopes and bytes, not product payload semantics.
- The composition root depends on domains; domains never depend on the composition
  root.
- A move is incomplete until tests patch or inject the new owner rather than relying
  on the old facade's globals.

### Lifecycle

- Resource ownership is explicit for threads, watchers, websocket connections,
  subprocesses, timers, observers, and bindings.
- Startup either returns an owned resource or fails without leaving a partial owner.
- Shutdown is idempotent and runs in strict reverse ownership order.
- Reinject/remove cycles must not increase listener, observer, timer, binding, or
  thread counts.

### Change Isolation

- Mechanical moves do not include behavioral optimization.
- A phase moves one ownership slice, migrates its tests, and passes its exit gate
  before the next slice begins.
- Compatibility facades remain until the new owner is accepted. They are not removed
  midway through a phase.

## Terminal Architecture

Dependencies point downward in this diagram:

```text
entry and compatibility
  cli.py                    runtime_orchestration.py
     |                              |
  cli_app.py                 daemon_runtime.py
                                   |
                            renderer_runtime.py
                                   |
          +------------------------+------------------------+
          |                        |                        |
  renderer_event_loop.py   renderer_startup.py      runtime_context.py
          |                        |                        |
  renderer_bridge.py       codex_app_runtime.py     snapshot_builder.py
  renderer_connection.py   instance_lock.py         runtime_commands.py
  renderer_file_events.py  runtime_paths.py         runtime_config.py
          |                        |                session_snapshots.py
          +------------------------+------------------------+
                                   |
          +------------------------+------------------------+
          |                        |                        |
      usage domain             overlay domain          renderer Python
  usage_contributions.py   overlay_projection.py    renderer_payloads.py
  usage_cache.py           desktop_overlay.py       renderer_presenters/
  usage_insights.py        loading_feedback.py      renderer_catalog.py
  session_cleanup_runtime.py overlay_commands.py    renderer_client.py
  active_work.py           overlay_ipc.py           renderer_cdp/
          |                        |                        |
          +------------------------+------------------------+
                                   |
          runtime_usage.py / runtime_settings.py / runtime_policies.py
          core / config / platforms / filesystem and storage primitives

renderer asset build
  ui/renderer_script.py -> ui/renderer_bundle.py -> ui/renderer_assets/**
```

The diagram shows allowed direction, not a requirement for one class per file. New
cycles or facade-to-domain back edges block the phase.

### Runtime Entry And Coordination

| Module | Terminal responsibility | Size guardrail |
|---|---|---:|
| `cli.py` | Explicit supported compatibility exports | at most 80 lines |
| `runtime_orchestration.py` | Composition root, entry forwarding, explicit compatibility exports | at most 400 lines |
| `cli_app.py` | Argument parser, update/once commands, `main` | 250-350 lines |
| `daemon_runtime.py` | Daemon lifecycle, renderer-only legacy stubs, restart policy | 250-400 lines |
| `renderer_runtime.py` | One renderer session's bootstrap, resource ownership, and shutdown | 300-450 lines |
| `renderer_event_loop.py` | Typed event reduction, refresh execution, domain pushes, and next wait | 850-1,100 lines |
| `renderer_wait.py` | Pure wait ports, scheduled deadlines, and wait-delay planning | 100-180 lines |
| `renderer_bridge.py` | Renderer callback normalization and runtime event publication | 250-350 lines |
| `renderer_connection.py` | Connection health, probes, follow healing, and lightweight pushes | 350-500 lines |
| `renderer_file_events.py` | Watch specs, debounce, overflow reconciliation, degraded diagnostics | 250-350 lines |

`run_renderer_hud_session` becomes a lifecycle/composition function, not a nested
application. Coordinator methods should normally remain below 150-200 lines.

### Runtime Services And Domains

| Module group | Terminal responsibility | Size guardrail per module |
|---|---|---:|
| `runtime_context.py`, `runtime_config.py` | Owned resources and explicit configuration construction/reload | 300-450 lines |
| `snapshot_builder.py`, `session_snapshots.py` | Snapshot build/enrichment and selected-session preview cache | 200-650 lines |
| `runtime_commands.py` | Settings/cleanup/background command execution through ports | 500-700 lines |
| `runtime_paths.py`, `runtime_diagnostics.py` | Paths and structured diagnostic sinks | 100-250 lines |
| `codex_app_runtime.py`, `renderer_startup.py`, `instance_lock.py` | OS process control, CDP startup policy, and singleton lifecycle | 150-850 lines |
| `usage_contributions.py`, `usage_cache.py` | Incremental per-file contribution state and query facade | 450-800 lines |
| `usage_insights.py`, `session_cleanup_runtime.py` | Projection/worker and prepare-commit-discard cleanup transaction | 300-700 lines |
| `active_work.py` | Recent work discovery, tool/work state, and subagent filtering | 700-900 lines |
| `overlay_projection.py` | Pure item projection, ordering, limiting, and stabilization | 350-500 lines |
| `desktop_overlay.py`, `loading_feedback.py` | Detached helper supervision/state and loading helper lifecycle | 350-850 lines |
| `overlay_commands.py`, `overlay_ipc.py` | Command routing and versioned sidecar contract | 100-450 lines |

Line counts are review guardrails, not the goal. A smaller file with cyclic imports or
hidden global ownership still fails the architecture.

### Renderer Python

```text
ui/renderer_hud.py                 # explicit compatibility facade
ui/renderer_domains.py             # explicit compatibility facade, <= 200 lines
ui/renderer_script.py              # build/constant compatibility facade
ui/renderer_bundle.py              # ordered manifest, resource loading, boot injection

renderer_catalog.py                # model catalog and boot configuration
renderer_payloads.py               # full/partial payload schemas and domain selection
renderer_payload_builder.py        # snapshot-to-full/light payload projection
renderer_presenters/
  session.py
  budget.py
  request.py
  activity.py
renderer_client.py                 # install/update/bootstrap/close lifecycle
renderer_cdp/
  __init__.py                      # explicit old-path exports, <= 100 lines
  target.py                        # target discovery and selection
  connection.py                    # persistent websocket connection
  bindings.py                      # binding envelope and callback transport
```

`renderer_cdp` is a package in the terminal layout; there is not both a
`renderer_cdp.py` file and a same-named directory. CDP modules cannot import payload,
settings, overlay, or runtime-loop modules.

### Renderer JavaScript And CSS

The terminal build remains one ordered script injected as one IIFE. Do not convert
the injected runtime to browser ES modules or multiple runtime `eval` calls.

```text
ui/renderer_assets/
  00_kernel.js
  shared.js
  model_picker.js
  theme.js
  active_session.js
  layout.js
  composer.js
  session_view.js
  budget.js
  diagnostics.js
  settings_shell.js
  usage_insights.js
  session_cleanup.js
  background_usage.js
  rest_reminder.js
  99_bootstrap.js
  styles/
    base.css
    panels.css
    settings.css
    storage.css
    background_usage.css
    overlays.css
```

Executable JS domain files should normally stay below about 1,500 lines and CSS
fragments below about 1,200 lines. Domains use kernel/shared capabilities and do not
read another domain's private variables. `settings_shell` owns modal chrome and
dispatch only; settings subdomains register tabs/actions. The payload router calls a
registry rather than hard-coding private domain functions.

## Contracts To Establish Before Moves

### Runtime Events And Refresh Plans

Runtime event sources publish typed events such as:

- renderer active-session change;
- current session append/rotation;
- session map/state database change;
- settings command or file change;
- budget boundary;
- renderer layout/theme/attachment event;
- overlay command/state event;
- connection health event;
- runtime diagnostic event;
- explicit retry, keepalive, and reminder deadline.

A pure reducer maps `(LoopState, RuntimeEvent)` to `(LoopState, RefreshPlan)`. A plan
states which snapshot inputs are invalid, which partial payload domains are needed,
and whether overlay projection is needed. Executors perform the work; the reducer
does not touch files, processes, CDP, Qt, or clocks.

### Dependency Ports

The composition root constructs an aggregate of narrow protocols, provisionally
named `RuntimeServices`:

```python
RuntimeServices(
    clock=ClockPort(...),
    events=RuntimeEventSourcePort(...),
    snapshots=SnapshotBuilderPort(...),
    renderer=RendererClientPort(...),
    overlay=OverlayPort(...),
    app=CodexAppPort(...),
    storage=RuntimeStoragePort(...),
)
```

The aggregate contains dependencies, not business logic. Command handling may use a
smaller `CommandServices` view. Tests inject fake clocks, watchers, clients, bridges,
overlays, process controllers, storage, and snapshot builders. They do not patch a
compatibility facade and assume the patch changes another module's globals.

### Snapshot And Payload Contracts

- Snapshot inputs and enrichment steps have explicit DTOs or protocols.
- Full and partial renderer payloads have schema contract tests.
- Provider filtering stays above raw contribution-cache semantics.
- Payload apply order remains:

```text
currentSession -> sessionSwitch -> budget -> settings -> overlay ->
backgroundUsage -> diagnostics -> usageInsights -> sessionCleanup
```

- `startup` remains a bootstrap domain and is removed when authoritative session
  payloads arrive.

### Overlay IPC Contract

`overlay_ipc.py` owns only:

- versioned state, command, acknowledgement, and transition envelope shapes;
- sidecar path construction;
- command correlation/matching;
- transition names and validation.

It does not import PySide, launch subprocesses, project work items, activate Codex,
or publish runtime events. Those responsibilities belong to `desktop_overlay.py`,
`overlay_projection.py`, and `overlay_commands.py` respectively.

### Renderer Public ABI

Freeze and test:

- `window.__codexUsageHudUpdate` and `window.__codexUsageHudRemove`;
- active-session, theme, layout, settings, cleanup, background, and audit binding
  names;
- storage keys and retained window-state keys;
- payload apply order;
- selection/applied sequence semantics;
- install, reinject, root replacement, hydration, and remove behavior.

Only one boot placeholder is allowed in the assembled asset. It is replaced once by
one boot configuration containing the model catalog and feature flags. The builder
fails if the placeholder count is not exactly one or a placeholder remains.

### Public Python Compatibility

P0 records an explicit allowlist for supported imports used by entry points and
tools. Compatibility means import and call behavior, not monkeypatch propagation.
The following are known consumers that require inventory before facade removal:

- build entry points using `cli.main`;
- tools using `cli.UsageSummaryCache`;
- tools using `cli.current_budget_windows`;
- tools using `cli.renderer_diagnostic_path`.

## Test Ownership And Baseline Policy

### Test Decomposition

Split runtime tests by implementation owner:

```text
tests/test_usage_contributions.py
tests/test_usage_cache.py
tests/test_usage_insights.py
tests/test_session_cleanup_runtime.py
tests/test_active_work.py
tests/test_overlay_projection.py
tests/test_desktop_overlay.py
tests/test_overlay_commands.py
tests/test_runtime_commands.py
tests/test_snapshot_builder.py
tests/test_renderer_event_loop.py
tests/test_renderer_startup.py
tests/test_daemon_runtime.py
tests/test_cli_app.py
tests/test_legacy_compat.py
```

Split renderer tests by contract level:

```text
tests/test_renderer_payloads.py
tests/test_renderer_bundle.py
tests/test_renderer_dom.py
tests/test_renderer_client.py
tests/test_renderer_cdp.py
tests/test_renderer_compat.py
```

The compatibility inventory now reports `0 paths, 0 references` for the tracked
legacy facade prefixes. `tests/test_renderer_hud.py` remains the bundle and DOM
contract suite; these counts are migration evidence, not APIs to preserve.

Rules:

- Patch the implementation owner, or preferably inject a fake port.
- Keep only public-facade behavior in compatibility tests.
- Before moving a symbol, make its tests owner-correct in the same phase.
- Do not remove the `sys.modules` aliases until old-path patches are zero outside
  dedicated compatibility tests.
- Do not replace a module alias with ordinary re-exports while tests still expect
  patches to modify implementation globals; that fails silently.

### Baseline Failure Manifest

P0 creates a machine-readable baseline with exact node IDs and normalized failure
fingerprints. It records commit, dirty state, diff hash when dirty, command, Python,
pytest, PySide/Qt, OS, locale, timezone, run counts, owner, provenance, expiry, and
removal condition.

Policy:

- unexpected failure: block;
- fingerprint mismatch: block;
- unexpected pass: block and remove the entry;
- expired entry: block;
- flaky candidate: never place in a release allowlist;
- broad exclusions, class wildcards, and `-k not ...`: forbidden.

Current working-tree evidence has these candidates, but they are not a confirmed
HEAD baseline until P0 repeats them from a clean `git archive HEAD` with a recorded
environment:

1. `tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_holds_switch_completed_for_next_update`
2. `tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_skips_unchanged_state_until_keepalive`
3. `tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_starts_when_pyside6_available`
4. `tests/test_ui.py::BudgetHelperTests::test_desktop_work_overlay_suppresses_first_snapshot_items`

The following test is a timing/order flaky candidate, not an allowed deterministic
failure:

```text
tests/test_ui.py::BudgetHelperTests::test_work_overlay_helper_retries_transient_state_read
```

P0 isolates it with a subprocess and fake clock/event scheduling or fixes its test
contract. The four overlay failures expire no later than P2; P2 cannot exit while
they remain allowlisted.

## Fixed Phase Order

| Phase | Dependency | Primary result | Hard exit |
|---|---|---|---|
| P0 Baseline and test decoupling | Current checkpoint | Trusted baseline, ports, ownership tests | No ambiguous baseline or high-risk facade patching |
| P1 Usage domain | P0 | Incremental usage, insights, cleanup transaction owners | Incremental/full equality and latency pass |
| P2 Overlay domain | P0, P1 contracts | Contract/projection/supervision/commands separated | Isolated Qt plus real helper pass; overlay baseline entries gone |
| P3 Snapshot and settings services | P1, P2 | Explicit context, snapshot, command services | Partial/full payload and command correlation pass |
| P4 Runtime coordinator | P3 | Small lifecycle owner plus pure event reducer | Idle no-work proof and live runtime gate pass |
| P5 Renderer Python | P3, P4 ports | Payload/client/CDP transport separated | Schema, disconnect, and source/wheel resource checks pass |
| P6 Renderer JS/CSS | P5 | Ordered domain asset bundle with explicit lifecycle | DOM, reinject, screenshot, and package gates pass |
| P7 Startup, daemon, and CLI | P4, P5 | OS startup and entry points separated | Facade size/import/build gates pass |
| P8 Compatibility removal and final acceptance | P1-P7 | Explicit facades and full release evidence | Baseline empty; Windows/macOS/package/live acceptance complete |
| P9 Follow-up runtime/overlay/activity owners | P7-P8 | Pure supervision, session assembly, and top-level activity owners | Owner tests, dependency audits, full suite, and package smoke pass |
| P10 Renderer asset/activity owners | P6, P9 | Byte-preserving asset subdomains and pure activity trail owner | Exact template hash, owner tests, full suite, and package smoke pass |
| P11 Renderer event/overlay/request owners | P9-P10 | Pure event reduction, command channel, and request projection owners | Owner tests, dependency audits, full suite, and package smoke pass |

Do not reorder P6 ahead of P5: the JS split needs stable payload and client
boundaries. Do not start P1-P7 production moves before P0 exits.

## P0 - Baseline And Test Decoupling

### Work

1. Capture a clean `HEAD` archive baseline and a separate current-working-tree
   candidate baseline. Record commit, dirty state, commands, environment, artifacts,
   and normalized fingerprints.
2. Create separate confirmed-failure and flaky-candidate manifests plus a gate that
   treats new failures, changed fingerprints, expired entries, and XPASS as blocking.
3. Inventory every public import, old facade patch path, module-global replacement,
   JS public global, binding name, storage key, timer, observer, and package-data
   asset.
4. Split test ownership scaffolding. Move control-plane tests first; domain tests may
   move with their owning P1-P3 slice, but every old patch path must be classified.
5. Introduce narrow runtime ports, fake implementations, a fake clock, deterministic
   event source, and lifecycle recorder.
6. Add architecture tests for import direction, facade export allowlists, forbidden
   reverse dependencies, and resource/package manifest completeness.
7. Freeze the current assembled renderer script hash, byte length, ABI inventory,
   payload order, lifecycle inventory, and representative real screenshots.
8. Convert shared-state Qt timing tests to subprocess isolation and fake scheduling.

### Exit Gate

- The baseline is reproducible from the recorded clean source and environment.
- The flaky candidate is deterministic or repaired; it is not allowlisted.
- No test migration uses broad exclusion.
- Runtime control-plane tests use injected ports rather than facade-global patches.
- Architecture and public-export tests run in the standard phase gate.
- The renderer contract snapshot and package-data inventory are checked in.

## P1 - Usage Data Domain

### Work

1. Move per-file state, tail offsets, contribution replacement, and reconciliation to
   `usage_contributions.py`.
2. Keep `UsageSummaryCache` as a query/invalidation facade in `usage_cache.py`.
3. Move insights DTOs, projection, grouping, and worker lifecycle to
   `usage_insights.py`.
4. Move deleted-usage prepare/commit/discard and cleanup worker integration to
   `session_cleanup_runtime.py`.
5. Preserve provider filtering above raw contribution-cache semantics.
6. Keep a temporary explicit facade export for callers, while all usage tests patch
   or construct the real owners.

### Required Proof

- Full rebuild and incremental contribution replacement produce equal results.
- Append, truncate, rotation, parser-version change, deleted ledger, and rollback
  scenarios pass.
- Insights and family lifetime usage remain stable across provider scopes.
- A single file append does not trigger an all-session reparse.
- The latency harness stays within its recorded regression budgets.

### Exit Gate

- No usage implementation remains in `runtime_orchestration.py`.
- Usage tests have zero `codex_usage_hud.cli.*` patches except explicit facade tests.
- Cache, insights, and cleanup modules have no coordinator/facade imports.

## P2 - Overlay Domain

### Work

1. Finalize versioned state/command/ack/transition contracts in `overlay_ipc.py`.
2. Move pure work-item/background/rest projection, ordering, limiting, visible cache,
   and stabilization to `overlay_projection.py`.
3. Move PySide dependency probing, helper supervision, atomic state publication,
   heartbeat, restart, and command watcher lifecycle to `desktop_overlay.py`.
4. Move loading/restart feedback helper lifecycle to `loading_feedback.py`.
5. Move session switch/refocus and command routing to `overlay_commands.py`, using
   explicit app activation and runtime-event ports.
6. Inject the command handler into the command pump; the helper must not import the
   coordinator.

### Required Proof

- Unchanged state does not rewrite the sidecar except an explicit visible-item
  keepalive.
- State writes are atomic; retries use fake timing; command correlation and transition
  artifacts are exact.
- Qt tests run in subprocess isolation with no shared QApplication or watcher state.
- Lazy PySide import and Windows/macOS import safety pass.
- One real helper run verifies state JSON, command JSON, transition log, click routing,
  completion hold, and clean shutdown.

### Exit Gate

- The four deterministic overlay baseline candidates are fixed or their contract is
  explicitly corrected; none remain in the manifest.
- `overlay_ipc.py` is contract-only.
- Overlay modules do not import `runtime_orchestration` or `cli`.
- No new Qt/Tk product behavior is introduced.

## P3 - Snapshot And Settings Services

### Work

1. Introduce `RuntimeContext` as owned resources and immutable configuration handles,
   not a container for unrelated business methods.
2. Extract path/provider/config resolution and reload into `runtime_config.py`.
3. Extract selected-session preview, cold hydration, and stale sequence checks into
   `session_snapshots.py`.
4. Extract snapshot construction, budget/current-session/activity enrichment, and
   text projection into `snapshot_builder.py`.
5. Replace `_handle_renderer_settings_command` with a small dispatcher whose handlers
   live in `runtime_commands.py` and receive updater, overlay, workers, storage, and
   clock through ports.
6. Keep pure merge, changed-key classification, response envelope, and partial-domain
   rules in `runtime_settings.py`.

### Required Proof

- Cold preview, canonical hydration, pending mapping, incremental current session,
  visible-first budget reuse, and stale rejection pass.
- Full snapshots and partial settings/background/cleanup responses satisfy schemas.
- Request IDs correlate command, acknowledgement, completion, and error responses.
- Settings handlers do not reach module globals or instantiate hidden workers.
- Session deletion preserves prepare/execute/refresh/commit-or-discard ordering; the
  UI closes only after the matching response refreshes the list.

### Exit Gate

- Snapshot construction and settings side effects have one owner each.
- `RuntimeContext.close()` is idempotent and its owned-resource order is tested.
- Snapshot/command tests use injected services and owner imports.

## P4 - Runtime Coordinator

### Work

1. Extract watcher specs, reconciliation, debounce, and degraded state to
   `renderer_file_events.py`.
2. Extract binding/callback normalization to `renderer_bridge.py`.
3. Extract connection health, lightweight push, probe, and follow healing to
   `renderer_connection.py`.
4. Implement the pure event reducer and refresh executor in
   `renderer_event_loop.py`.
5. Reduce `run_renderer_hud_session` in `renderer_runtime.py` to dependency assembly,
   bootstrap, loop start, and strict reverse-order shutdown.
6. Represent retry, budget, reminder, and keepalive deadlines as scheduled events.
   Preserve their current semantics during the move.

### Required Proof

- Fake-clock scenarios cover event coalescing, mapping wake, layout-only update,
  settings partial update, retry, disconnect/reconnect, and shutdown during work.
- An idle scenario proves counters for snapshot build, session scan, overlay project,
  and CDP push remain unchanged.
- File overflow reconciles; degraded polling is diagnostic and conservatively backed
  off.
- Connection loss is explicit and bounded; it does not silently create an
  unconditional target scan loop.
- A real Renderer run captures overlay artifacts, transition logs, active-session
  behavior, current-session latency, and an idle CPU sample.

### Exit Gate

- The old 2,391-line nested renderer function no longer owns handlers or business
  logic.
- The coordinator sequences services; it does not implement usage, settings,
  overlay, CDP, or presentation rules.
- `no event -> no work` passes deterministic and live evidence gates.

## P5 - Renderer Python Domains And CDP Transport

### Work

1. Move model catalog and boot configuration to `renderer_catalog.py`.
2. Move full/partial payload schemas and domain selection to
   `renderer_payloads.py`.
3. Move session, budget, request, and activity presentation into
   `renderer_presenters/`.
4. Move install/update/bootstrap/close lifecycle to `renderer_client.py`.
5. Convert `renderer_cdp.py` into the `renderer_cdp/` package and separate target,
   connection, and binding transport.
6. Keep `ui/renderer_domains.py`, `ui/renderer_hud.py`, and
   `renderer_cdp/__init__.py` as explicit compatibility facades while callers migrate.

### Required Proof

- Full and partial payload schema fixtures remain compatible.
- Binding envelopes, target selection, persistent connection, disconnect, timeout,
  and close behavior pass with fake and local CDP endpoints.
- Client reinjection and update ordering remain unchanged.
- Transport modules cannot import product payload/runtime modules.
- Source checkout and installed wheel imports behave the same.

### Exit Gate

- `ui/renderer_domains.py` is at most 200 lines and transport-free.
- `renderer_cdp/__init__.py` is at most 100 lines and exposes only the approved
  compatibility surface.
- No websocket or binding protocol redesign is mixed into the move.

## P6 - Renderer JavaScript And CSS Domains

P6 has a fixed internal order. Each subphase is independently reviewable and must
pass before the next one.

### P6.1 Contract Refresh And Mechanical Asset Split

- Re-capture the P0 bundle hash/length, globals, bindings, keys, payload order,
  timers, observers, screenshots, and DOM behavior immediately before the split.
- Extract continuous CSS and JS source fragments in their original order.
- Join with a fixed manifest so the assembled script is byte-for-byte identical to
  the pre-split asset.
- Do not add function wrappers, change scope, rename variables, or normalize
  whitespace/newlines in this subphase.

### P6.2 Kernel And Shared Capabilities

- Introduce `ctx`, a domain registry, retained payload state, one frame scheduler per
  concern, storage/binding adapters, and a teardown ledger.
- Keep one IIFE and one boot placeholder.
- Teardown runs in reverse registration order and accounts for every listener,
  observer, animation frame, timeout, and interval.

### P6.3 Leaf Domains

Migrate in this order:

```text
model_picker -> theme -> diagnostics -> budget -> rest_reminder -> session_view
```

Each domain implements an install/apply/dispose contract and uses only kernel/shared
capabilities.

### P6.4 Settings Domains

Migrate:

```text
usage_insights -> session_cleanup -> background_usage -> settings_shell
```

`settings_shell` retains only modal chrome and tab/action dispatch. It cannot read
subdomain private state.

### P6.5 DOM-Sensitive Domains

Migrate `layout` and then `composer`. Preserve anchor caching, targeted observers,
gesture state, resize behavior, attachment behavior, frame scheduling, and narrow
window behavior.

### P6.6 Active Session Last

Migrate `active_session` only after other domains are stable. Preserve:

- exact canonical thread ID authority;
- provisional `client-new-thread:*` handling;
- cached preview behavior;
- `selectionSeq` and `appliedSeq` stale rejection;
- reinject sequence restoration before remove;
- Top10/session link interaction isolation.

Cached preview must never advance the authoritative applied sequence.

### P6.7 Router And Bootstrap Cutover

- Switch payload application to the registry in the frozen domain order.
- Remove legacy fragment code only after equivalent domain lifecycle coverage exists.
- Verify retained-domain hydration after Codex replaces the root while the JS realm
  survives.
- Keep CSS manifest order fixed and emit one `<style>` element.

### Required Proof

- P6.1 assembled bytes match exactly.
- Manifest uniqueness/order and placeholder completeness pass.
- Public globals, bindings, state keys, storage keys, and payload order pass.
- Repeated install/update/remove and root replacement do not grow lifecycle counts.
- Browser behavior tests cover startup, every settings tab, theme, drag/resize,
  composer/attachments, session switching, deletion loading/refresh/close, and
  760/520 narrow windows.
- Real Codex App screenshots/interactions show no visible regression.
- `renderer_assets/**` is available via `importlib.resources` from source, wheel, and
  PyInstaller onefile builds.

### Exit Gate

- `ui/renderer_script.py` is a small facade over the bundle builder.
- Executable JS and CSS meet the size guardrails or have a documented single-owner
  exception.
- No domain accesses another domain's private state.
- Single-script injection and the frozen ABI remain intact.

## P7 - Startup, Daemon, And CLI

### Work

1. Move runtime paths and diagnostics to their dedicated modules.
2. Move Windows/macOS process discovery, verified process-family control, launch,
   stop/restart, activation, and window readiness to `codex_app_runtime.py`.
3. Move CDP candidate/state/port policy, startup scenarios, and bounded recovery to
   `renderer_startup.py`.
4. Move singleton PID/lock behavior to `instance_lock.py`.
5. Move daemon lifecycle and renderer-only legacy stubs to `daemon_runtime.py`.
6. Move parser, update/once behavior, and `main` to `cli_app.py`.
7. Reduce `runtime_orchestration.py` to composition and explicit compatibility
   forwarding.

### Required Proof

- Windows and macOS process audit and import behavior pass.
- Configured/last-successful CDP port, bounded fresh-port recovery, single launch,
  plain-launch takeover, window readiness, and restart recovery pass.
- CLI Codex processes are never included in the desktop restart set.
- `python -m codex_usage_hud --help`, once, daemon, stop, update, and legacy display
  aliases pass.
- Imports do not eagerly require PySide.
- Wheel and PyInstaller entry points use the explicit facade/CLI owners.

### Exit Gate

- `runtime_orchestration.py` is at most 400 lines.
- `cli.py` contains no implementation and has an explicit export list.
- No domain imports either facade.
- Source, wheel, and executable startup smoke tests pass.

## P8 - Compatibility Removal And Final Acceptance

### Work

1. Drive old `codex_usage_hud.cli.*` and renderer-facade patch references to zero
   outside dedicated compatibility tests. **Completed:** the checked-in facade
   inventory is `0 paths / 0 references`.
2. Replace `sys.modules` identity aliases with explicit allowlisted exports and entry
   forwarding. **Completed:** `cli.py` and `ui/renderer_domains.py` now expose only
   explicit bindings; dynamic `__getattr__` and module identity aliases are gone.
3. Remove obsolete migration adapters, duplicate exports, stale baseline entries, and
   temporary bundle comparison fixtures.
4. Run import-cycle and ownership audits against the terminal graph.
5. Reconcile strategy, checklist, completion audit, live runbook, macOS validation,
   packaging, and release documentation with current evidence.

### Final Acceptance

- Full automated test suites pass with an empty failure manifest and no flaky
  allowlist.
- Source checkout, installed wheel, and PyInstaller executable pass smoke tests.
- Windows real Renderer acceptance covers startup, active session, current request,
  all settings tabs, overlay commands/artifacts, root replacement, reinject/remove,
  theme, drag/resize, narrow windows, and idle CPU.
- macOS package/import/startup smoke and Renderer interaction smoke pass.
- Latency regression budgets pass and live evidence distinguishes measured pipeline
  latency from visible end-to-end latency.
- No page/session/config/filesystem event means no recurring snapshot, scan, or CDP
  push work.
- Compatibility facades expose only the documented allowlist; private monkeypatch
  propagation is intentionally unsupported.

P8 is the release-acceptance gate. P9 is a post-P8 structural follow-up that
keeps the same dependency and behavior constraints while the live acceptance
evidence is still being collected.

## P9 - Follow-up Runtime, Overlay, And Activity Owners

### Work

1. Extract one-shot renderer context/resource/bridge/client/session-adapter
   construction into `renderer_runtime_assembly.py`; retain startup plan,
   window/CDP attachment, event-loop wiring, and final reverse shutdown in the
   composition root and their existing owners.
2. Extract pure detached-overlay helper health, availability, backoff,
   keep-alive, and system-action classification into `overlay_supervision.py`;
   keep subprocess, watcher, state, diagnostic, and Qt side effects in
   `desktop_overlay.py`.
3. Extract pure top-level task/activity/trail projection into
   `renderer_activity_projection.py` with explicit callback context; keep final
   payload envelope assembly and public compatibility wrappers in
   `renderer_payload_builder.py`.
4. Add owner-level tests and architecture checks for dependency direction,
   partial construction cleanup, pure-policy imports, and payload projection
   behavior.

### Exit Gate

- P9 owner tests and the full automated suite pass with existing skips only.
- New owners do not import facades, runtime composition roots, CDP clients, or
  Qt/process side effects outside their declared boundary.
- Source, fresh-wheel, and PyInstaller smoke checks include the P9 owners.
- The existing Renderer payload ABI, overlay state/command semantics, and
  reverse-order resource shutdown remain unchanged.

## P10 - Renderer Asset And Activity Owners

### Work

1. Keep the manifest-level `12_layout` asset intact while splitting its raw
   style, markup, gesture, anchor, and observer fragments into explicit Python
   owners joined in the original byte order.
2. Extract the continuous support/about settings markup into
   `settings_support_panels.py`; retain the existing settings-shell closure,
   bindings, lifecycle calls, and fixed manifest order.
3. Extract activity-trail event filtering, deduplication, and formatting into
   `renderer_activity_trail.py`; retain the projection compatibility wrapper
   and final payload envelope owner.
4. Add exact template/hash, fragment-join, static-subdomain, and direct-vs-wrapper
   behavior tests.

### Exit Gate

- `RENDERER_HUD_SCRIPT_TEMPLATE` remains 623712 bytes with the frozen SHA-256.
- The manifest remains 18 ordered assets and still ends with `15_router`.
- P10 owners have no forbidden runtime/facade/Qt/CDP reverse dependencies.
- Full automated tests, compileall, inventory, Ruff, wheel import, and
  PyInstaller archive/help smoke pass.

## P11 - Renderer Event, Overlay Channel, And Request Projection Owners

### Work

1. Extract pure renderer event-to-refresh-plan reduction and event-batch
   coalescing into `renderer_event_reduction.py`; retain explicit public
   re-exports and event-loop side effects in `renderer_event_loop.py`.
2. Extract incremental overlay command JSONL tailing, request-id
   deduplication, and acknowledgement append into `overlay_command_channel.py`;
   retain watcher, helper process, state revision/order, diagnostics, and Qt
   side effects in `desktop_overlay.py`.
3. Extract request/task-round projection, totals, row formatting, and row
   details into `renderer_request_projection.py` with an explicit formatting
   context; retain final payload envelope and compatibility wrappers in
   `renderer_payload_builder.py`.
4. Add owner-level tests and architecture checks for public re-exports,
   sidecar contracts, wrapper equivalence, dependency direction, and no-event
   behavior.

### Exit Gate

- P11 owner tests and the full automated suite pass with existing skips only.
- New owners do not import facades, runtime composition roots, CDP clients,
  Qt/process side effects, or unrelated owner internals.
- Existing Renderer payload, overlay command/state, shutdown, and no-event
  semantics remain unchanged.
- Source, fresh-wheel, and PyInstaller smoke checks include all P11 owners.

## Validation Matrix

Every phase runs:

```powershell
python -m compileall -q src tests tools
ruff check <changed modules and tests>
git diff --check
python -m pytest tests/test_architecture.py <owning-domain tests> -q
```

P0 defines one stable full non-UI command and one subprocess-isolated Qt command.
Those commands are then reused unchanged; they must not grow phase-specific
`-k not ...` clauses.

| Change type | Additional gate |
|---|---|
| Usage/cache | Full-vs-incremental reconciliation and latency harness |
| Overlay | Subprocess-isolated Qt suite plus real state/command/transition artifacts |
| Snapshot/settings | Full/partial schema fixtures and correlated command scenarios |
| Runtime loop | Fake-clock event scenarios, idle no-work counters, live CPU/latency |
| Renderer Python/CDP | Disconnect/reconnect, target lifecycle, source/wheel import |
| Renderer JS/CSS | Bundle contracts, browser DOM behavior, lifecycle counts, real screenshots |
| Startup/CLI | Cross-platform import, daemon scenarios, wheel and executable smoke |
| Final | Full UI/non-UI, latency, Windows live acceptance, macOS smoke |

Verbose build and packaging output must be redirected to log files; only a concise
summary or log tail should enter review context.

## Risk Register

| Risk | Why it is dangerous | Control |
|---|---|---|
| Facade monkeypatch drift | A patch succeeds but no longer changes the real owner | P0 ports, owner patches, explicit compatibility tests, alias removal last |
| JS scope/hoisting change | Wrapping fragments can silently change closure behavior | Byte-identical split first; leaf domains first; active session last |
| Active-session sequence reset | Cached preview can overwrite authoritative state | Freeze and test `selectionSeq`/`appliedSeq`; restore before remove |
| Lifecycle leaks | Reinject leaves observers/timers/listeners behind | Kernel teardown ledger and repeated-cycle count assertions |
| CSS cascade change | Unit tests may pass while layout changes | Fixed manifest order plus real screenshots and narrow-window checks |
| Package-data omission | Source works while wheel/onefile fails | `importlib.resources` and source/wheel/PyInstaller smoke in P5/P6 |
| Qt shared-state timing | Suite order creates false failures | Subprocess isolation, fake clock/events, no flaky allowlist |
| Startup/process regression | A structural move can restart the wrong process | Separate process audit from port policy; scenario tests on both platforms |
| Idle work reintroduced | A convenient timer defeats product direction | Typed deadlines, fake-clock idle counters, live CPU sampling |
| Stale documentation | Historical `Done` language overstates current proof | Update the audit at every phase exit; current evidence only |

## Phase Reporting And Stop Rules

At each phase boundary, record:

- before/after ownership map and file sizes;
- exact changed modules and moved symbols;
- commands and artifacts;
- baseline entries added, removed, or expired;
- latency and live checks run or not run;
- Windows/macOS evidence status;
- remaining compatibility exports and old-path patch counts;
- whether every exit condition passed.

Stop the phase and fix it before continuing when:

- a new failure appears;
- a baseline fingerprint changes or an entry XPASSes/expires;
- an import cycle or reverse dependency appears;
- idle counters show work without an event;
- visible renderer behavior changes during a mechanical slice;
- resource counts grow across reinjection or shutdown;
- source succeeds but wheel/executable resource loading fails.

Do not compensate by broadening a test exclusion, adding a polling fallback, restoring
Qt/Tk behavior, or moving the failure into a compatibility facade.

## Overall Definition Of Done

The refactor is complete only when all of the following are true:

- `runtime_orchestration.py` is at most 400 lines and owns only composition and
  explicit compatibility forwarding.
- `run_renderer_hud_session` only assembles services and owns lifecycle.
- `ui/renderer_domains.py`, `ui/renderer_hud.py`, `ui/renderer_script.py`, `cli.py`,
  and `renderer_cdp/__init__.py` are small explicit facades.
- No domain imports a facade or another domain's private implementation.
- Runtime, overlay, payload, transport, and JS lifecycle contracts are tested at
  their actual owners.
- `tests/test_ui.py` and `tests/test_renderer_hud.py` no longer act as giant
  implementation-coupled suites.
- The baseline manifest is empty and Qt timing coverage is deterministic.
- The renderer remains one injected IIFE with the existing public ABI and no visible
  UI regression.
- Idle runtime performs no recurring snapshot, session scan, or CDP payload work.
- Windows/macOS, source/wheel/executable, latency, real Renderer, and detached-helper
  acceptance evidence is current.

The P9, P10, and P11 owner slices are now implemented and verified. The next
implementation action is real-platform Renderer acceptance. Further structural
work may split the remaining layout-style or payload-presentation owners only
after another concrete owner contract is designed; it must not regress the
accepted P0-P11 moves.
