# Renderer Fallback Inventory

This inventory is the phase 0 checklist for converting renderer runtime fallbacks
into explicit diagnostics or opt-in legacy/debug behavior.

## Classification

| Class | Meaning | Target handling |
|-------|---------|-----------------|
| Delete | Conflicts with renderer-authoritative behavior | Remove after a DEBUG/runtime error path exists |
| Explicit error | Useful signal, unsafe as silent behavior | Emit `runtime_error` and show DEBUG error HUD |
| Diagnostic only | Still useful for troubleshooting | Keep behind explicit flag or manual command |
| Temporary fallback | Acceptable while replacing infrastructure | Mark degraded and add owner phase |

## Inventory

| Area | Current fallback | Risk | Target class | Next action |
|------|------------------|------|--------------|-------------|
| Active session | Renderer ref can become `renderer-unmatched` while resolver can still use other activity paths unless marked as new session | Wrong session can be shown after DOM/protocol drift | Explicit error | Done: renderer-unmatched now records `active_session.unmatched_thread` and no longer falls back to latest activity |
| Active session | `platform.get_active_conversation_ref()` CDP/native snapshot path | Multiple authorities in renderer mode | Diagnostic only | Done for renderer-authoritative tracker: no renderer selection now stays `renderer-waiting` instead of probing CDP/native; `--legacy-active-session-diagnostics` can opt in manually for diagnosis |
| Active session | `platform.detect_active_session()` newest JSONL fallback in `SessionPathResolver` | Can select a background/stale session | Delete for renderer mode | Done for renderer-authoritative tracker: `renderer-waiting` and `renderer-unmatched` do not scan latest JSONL |
| CDP target | Reinstall script and retry update after failed `__codexUsageHudUpdate` | Hides renderer script/API breakage until retries exhaust | Delete | Done: update failure now records `cdp.update_failed` and does not force reinstall/retry inside the same update |
| CDP target | Repeated page target discovery after failures | Can cost CPU and hide target churn | Explicit error | Cache target, invalidate on navigation/disconnect, surface `cdp.target_lost` |
| Settings command | Renderer writes command to `localStorage`; Python polls with `Runtime.evaluate` every second | Recurring CDP work with no user action | Delete | Done: settings commands use the settings bridge callback/event path; localStorage command write and Python polling API were removed |
| File watcher | Native watcher failure falls back to polling | Polling can scan all sessions repeatedly | Temporary fallback | Emit degraded diagnostic with mode/reasons |
| File watcher | Windows `ReadDirectoryChangesW` does not treat `bytes_returned == 0` as overflow | Can miss JSONL/session index changes | Explicit error | Reconcile directory and emit `file_watcher.overflow` |
| File watcher | macOS recursive sessions tree uses polling instead of recursive native events | High cost with large session trees | Temporary fallback | Evaluate FSEvents or watch current session plus index only |
| Usage summary | Full sessions scan on budget window/settings changes | Expensive but currently correct | Temporary fallback | Replace with file contribution table; keep full rebuild for rotate/parser-version changes |
| Current session usage | `JsonlSessionParser.parse_file()` rereads full current JSONL | Large sessions slow active updates | Delete | Add tail parser with offset and rotate/truncate rebuild |
| Renderer payload | Single full payload push for current session, budget, settings, overlay, diagnostics | Small changes update too much DOM | Temporary fallback | Split payload domains and keep full update as compatibility wrapper |
| Desktop overlay | PySide helper reads state file every 160 ms | Idle periodic IO/CPU | Delete | Replace with push IPC or watcher wakeup |
| Desktop overlay command | Helper command file is polled every 60 ms | Idle periodic IO/CPU | Delete | Replace with command event/IPC wakeup |
| Desktop overlay keepalive | Main process periodically rewrites overlay state | Needed only because helper polls/staleness logic | Temporary fallback | Remove after push IPC has explicit liveness |
| Legacy HUD | Tk/Qt entry points remain as compatibility stubs | Product confusion if revived | Diagnostic only | Keep stubs returning renderer-unavailable; do not add product behavior |

## Phase Order

1. Add runtime error model and DEBUG error HUD.
2. Emit diagnostics for unmatched renderer session, watcher degraded/overflow, CDP update failure.
3. Convert active-session fallback paths to explicit diagnostics.
4. Replace full-file current session parsing with a tail parser.
5. Replace settings and overlay polling with event channels.

## Validation

Use the local latency harness before and after each phase:

```powershell
python tools/measure_renderer_latency.py --markdown-output renderer_latency_baseline.md
```

The harness measures local parser/payload/cache/fallback-scan cost. It does not
measure live CDP transport or renderer paint latency.
