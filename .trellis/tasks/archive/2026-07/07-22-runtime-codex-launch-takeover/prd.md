# Handle Codex launches while HUD is alive

## Goal

Keep the HUD daemon alive across Codex Desktop exits and make the next external
Desktop launch converge to a usable renderer HUD without re-running the original
three startup scenarios incorrectly.

When a newly observed Desktop launch already declares a CDP port, attach to that
exact port after bounded readiness validation. When it is a verified new launch
without CDP, automatically relaunch that new Desktop family once with HUD-owned
CDP arguments. Existing or ambiguous Desktop processes remain protected by the
current explicit restart action.

## Background

- The existing startup contract distinguishes absent Desktop, running Desktop
  without a verified CDP target, and running Desktop with a verified target.
- `CodexDaemonManager` already survives a Desktop exit and waits for another
  verified Desktop process, but the next renderer startup does not know that the
  process was observed after a confirmed absence.
- CDP port parsing and endpoint/target validation already exist in `cli.py`.
- A freshly launched Desktop root process can appear several seconds before its
  renderer target. Immediate validation can therefore misclassify a valid
  external CDP launch as `restart-required`.
- The existing restart-loop fix requires one owner for stop -> port selection ->
  launch -> attach, with the launched state carried into the next attach.

## Requirements

- R1. Preserve the behavior and tests of the existing three startup scenarios.
- R2. Record when the daemon is waiting after a confirmed Codex Desktop exit and
  treat the next verified Desktop process family as a newly observed launch.
- R3. For a newly observed launch whose audited Desktop command lines declare one
  CDP port, wait up to the existing cold-start bound for `/json/version` and a
  main Codex page target, then attach to that exact port without restart UI.
- R4. For a newly observed launch with no declared CDP port, automatically stop
  only the verified Desktop process family, allocate a fresh bindable port,
  launch once with the existing debugger arguments, and attach without requiring
  confirmation.
- R5. Show only passive startup/relaunch progress for R3/R4. Retain the existing
  persistent restart action when process auditing is unavailable, the launch is
  not proven new, ports are ambiguous, or controlled recovery fails.
- R6. Carry launch ownership and expected port across the recovery transition so
  the HUD-owned process is never classified again as an external plain launch.
- R7. Continue excluding standalone/npm Codex CLI processes and never discover a
  CDP target by scanning a port range or trusting a listener alone.
- R8. Reuse the same runtime plan on Windows and macOS. A platform may retain a
  conservative, diagnosed process-snapshot fallback when a native start event is
  unavailable; no new renderer/page/session polling may be introduced.
- R9. Keep Qt/Tk behavior unchanged and do not add a legacy HUD fallback.

## Acceptance Criteria

- [ ] AC1. Existing absent/non-CDP/CDP startup tests remain green.
- [ ] AC2. After a daemon-managed Desktop exits, an externally launched Desktop
  with a non-default CDP port waits through delayed renderer creation and attaches
  to that exact port without offering restart.
- [ ] AC3. After the same confirmed-absence state, an externally launched Desktop
  without a CDP flag is stopped and relaunched exactly once with a fresh HUD-owned
  port, without a confirmation action.
- [ ] AC4. An ordinary Desktop that predates HUD waiting still uses the current
  explicit restart action and is never stopped automatically.
- [ ] AC5. CLI-only `codex.exe`, process-audit failure, and multiple distinct CDP
  ports cannot trigger automatic takeover.
- [ ] AC6. Stop, fresh-port selection, launch, and attach occur in that order, and
  a failed recovery cannot loop indefinitely.
- [ ] AC7. Focused daemon/startup tests, the full renderer gate, full pytest,
  compileall, and `git diff --check` pass.
- [ ] AC8. Windows live acceptance covers both new scenarios and verifies process
  command lines, `/json/version`, the selected page target, injected HUD DOM, and
  absence of duplicate Desktop roots or restart cards.
- [ ] AC9. macOS-specific process matching and state transitions have automated
  coverage; real-Mac acceptance is reported honestly if unavailable.

## Out Of Scope

- Replacing Start menu, taskbar, Dock, or application shortcuts with a HUD wrapper.
- Windows Image File Execution Options, injection, or another system-wide
  pre-execution interception mechanism.
- Port-range scanning or attaching to non-Codex DevTools targets.
- Redesigning the current renderer startup or restart surfaces.
