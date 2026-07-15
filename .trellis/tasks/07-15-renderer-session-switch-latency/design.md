# Design

## Data flow

`renderer binding -> canonical UUID -> exact SQLite lookup -> pending if unavailable -> state-db/session-index event -> invalidate cache -> exact lookup again -> sessionSwitch payload`。

The correction is intentionally narrow: classify `session-map` as latency-critical and wake immediately. The event remains coalesced in the existing source, so the main loop processes all accumulated reasons once. Cache invalidation stays in the loop before the synthetic `active_session_changed` event.

## Trade-off

An immediate wake can add one extra refresh during a state-db write burst. This is justified because it is the only event that makes an already-selected UUID resolvable, and the refresh remains visible-first/partial. Other change classes retain the existing debounce.

## Compatibility and rollback

No persisted format or public protocol changes. Reverting the focused event-classification change restores former behavior. The strict mapping contract is preserved.
