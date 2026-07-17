# Bug Analysis: Renderer session follow reliability

## 1. Root Cause Category

- **Category**: B/D/E - cross-layer contract, integration coverage gap, and implicit timing assumptions.
- **Specific cause**: renderer identity, Python mapping/snapshot work, CDP delivery, and Codex's own React click task were treated as one acknowledgement boundary. Provisional identity could be discarded, stale generations could win, and even a fast Python/CDP path could not execute renderer JavaScript until Codex finished 150-300 ms of synchronous route work.

## 2. Why Fixes Failed

1. Faster parsing and deferred work scans removed multi-second Python stalls but could not bypass the renderer main-thread queue.
2. Transport ACK and renderer apply timing proved delivery correctness, but `applyMs` began after CDP evaluation was scheduled and therefore hid the Codex click-task wait.
3. A persistent websocket removed connection setup cost, but an independent probe was fast only because it ran after the click task; it did not reproduce the binding-event timing.

## 3. Prevention Mechanisms

| Priority | Mechanism | Specific Action | Status |
|---|---|---|---|
| P0 | Architecture | Preserve raw identity and monotonic sequence through renderer, tracker, parser, and payload. | DONE |
| P0 | Runtime UX | Apply confirmed payload cache hits synchronously by exact ID without advancing the authoritative ACK. | DONE |
| P0 | Test coverage | Verify 50 real alternating selections, rapid A -> B -> C, and provisional-to-canonical convergence. | DONE |
| P1 | Observability | Attribute target discovery, persistent transport, fallback, renderer apply, and selection milestones separately. | DONE |
| P1 | Documentation | Record exact-ID cache and cached-preview ACK rules in the renderer runtime spec. | DONE |

## 4. Systematic Expansion

- **Similar issues**: any renderer feature that reacts to a Codex click and then sends `Runtime.evaluate` can be queued behind the same React event task.
- **Design improvement**: use renderer-local, exact-keyed projections for the first visible frame; retain Python/CDP as authority and reconciliation.
- **Process improvement**: performance acceptance must start at the renderer-observed event and distinguish page execution time from Python response receipt.

## 5. Knowledge Capture

- [x] Updated `.trellis/spec/backend/renderer-runtime-contracts.md`.
- [x] Added regression assertions for exact identity fields, cache markers, and cached-preview ACK exclusion.
- [x] Recorded real-app latency and correctness evidence in the task verification notes.

## Bug Analysis: PySide6 overlay state-read retry gap

### 1. Root Cause Category

- **Category**: B/C/D - cross-process contract, change propagation failure, and
  integration coverage gap.
- **Specific cause**: the original polling reader gave the 1.2 second failure grace
  repeated read opportunities. The event-driven `QFileSystemWatcher` migration
  removed that poll but did not add a bounded retry. A Windows callback during
  atomic replacement could therefore be the only read until the next 15 second
  keepalive, at which point the grace was already expired and the helper exited.

### 2. Why Fixes Failed

1. Atomic writer replacement prevented half-written JSON but did not guarantee
   destination readability at the exact watcher callback instant.
2. A read-failure grace without a retry source only delayed the failure; after the
   polling-to-watcher migration it no longer provided recovery.
3. Existing tests asserted atomic writes and watcher usage separately, but never
   ran the real Qt event loop through a transient first-read failure.

### 3. Prevention Mechanisms

| Priority | Mechanism | Specific Action | Status |
|---|---|---|---|
| P0 | Architecture | Preserve displayed bubbles and schedule one 80 ms retry through the watcher refresh path. | DONE |
| P0 | Test coverage | Force first-read `OSError` in a real offscreen PySide6 event loop and require recovery inside the grace. | DONE |
| P1 | Runtime | Keep persistent unreadability fatal, but distinguish it from one replace-window failure. | DONE |
| P1 | Documentation | Record atomic replace and watcher retry as one indivisible state-delivery contract. | DONE |

### 4. Systematic Expansion

- **Similar issues**: any file-event migration that removes a periodic fallback can
  silently remove retry, debounce, expiry, or reconciliation work previously owned
  by that loop.
- **Design improvement**: document event source and bounded recovery together; a
  grace interval is not a recovery mechanism unless something schedules re-entry.
- **Process improvement**: watcher tests must cover event-time transient failure,
  not only assert that polling was removed.

### 5. Knowledge Capture

- [x] Updated `.trellis/spec/backend/renderer-runtime-contracts.md` with the state
  delivery contract, matrix, wrong/correct example, and required regression.
- [x] Added a real PySide6 offscreen recovery test and watcher wiring assertions.
- [x] Recorded the eight runtime error occurrences and verification evidence in
  the task implementation notes.
