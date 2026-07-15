# Implementation plan

1. Add a focused immediate-wake rule for `session-map` file events, including mixed-reason batches.
2. Add regression coverage in the renderer file-event and renderer-loop tests.
3. Verify exact-mapping cache invalidation behavior in active-session tests.
4. Run required renderer tests, compile check, diff check, and latency harness.

## Risk points

- Do not broaden immediate wake to `sessions-root`; it can reflect high-frequency JSONL writes.
- Do not weaken exact UUID mapping to hide pending state.
