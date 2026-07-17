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
