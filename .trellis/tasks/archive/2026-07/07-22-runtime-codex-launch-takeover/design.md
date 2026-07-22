# Runtime Codex Launch Takeover Design

## Boundary

The existing startup classifier remains authoritative for a HUD invocation that
does not have process-arrival provenance. Runtime takeover is enabled only when
the daemon has first observed the prior Desktop family exit and then received a
new verified Desktop snapshot.

This is post-start takeover, not OS-level pre-launch interception.

## State And Contracts

Add runtime arrival provenance to the renderer entry:

```text
WAITING_FOR_CODEX
  -> observed external Desktop launch
      -> declared one CDP port: ATTACH_OBSERVED(port)
      -> declared no CDP port: RELAUNCH_OBSERVED
      -> audit/port ambiguity: RESTART_REQUIRED
```

`ATTACH_OBSERVED` owns the exact declared port even before it listens. It receives
the same cold-start window/target readiness bounds as a HUD-owned launch. It must
validate CDP protocol identity and the main Codex page before publishing success.

`RELAUNCH_OBSERVED` returns a dedicated daemon transition. The daemon displays a
passive progress card, stops the verified Desktop family, selects a fresh port,
launches exactly once, and re-enters the existing `attach-launched` path with
launch ownership set.

## Data Flow

1. The renderer session detects Desktop exit and returns
   `DAEMON_RESTART_REQUESTED`.
2. `run_daemon()` marks that the next `wait_for_codex()` result has fresh-launch
   provenance.
3. The startup planner audits current Desktop command lines.
4. One declared CDP port creates `attach-observed`; no declared port creates
   `relaunch-observed`; audit failure or conflicting ports creates the existing
   `restart-required` plan.
5. Successful attach persists the exact successful port and refreshes existing
   CDP-dependent runtime components.

## Safety

- Automatic stop is permitted only after a confirmed prior Desktop absence and a
  subsequent verified Desktop arrival.
- Standalone CLI processes are excluded by the existing executable-path filter.
- A process flag is discovery evidence, not success. `/json/version`, target
  listing, and main-page selection remain mandatory.
- Recovery is one-shot. The HUD-owned launch is tagged through the existing
  `launched_codex` path and cannot re-enter observed-launch classification.
- Process-audit failure, conflicting declared ports, or loss of provenance fails
  closed into the existing restart action.

## Compatibility

Windows keeps the existing Toolhelp listener and process-exit handle. The current
bounded wait scan remains a process-lifecycle fallback and is not copied into the
renderer loop.

macOS uses the same startup-plan contract. Add a lightweight macOS process
listener based on audited `ps` snapshots so daemon mode remains functional without
new dependencies; record it as a polling fallback until a reliable native launch
notification adapter is available.

## Rollback

The new behavior is isolated behind fresh-launch provenance and new startup plan
states. Removing those states and the daemon provenance flag restores the current
three-scenario behavior without data migration. Persisted CDP state format is
unchanged.
