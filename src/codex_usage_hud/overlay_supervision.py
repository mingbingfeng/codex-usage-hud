"""Pure helper-health decisions for the detached desktop overlay.

The desktop overlay owns subprocesses, sidecar files, and diagnostics.  This
module only evaluates an already-observed helper snapshot so those side effects
remain behind the existing owner boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


HEALTHY = "healthy"
EXITED = "exited"
RESTART = "restart"
DEFAULT_RUNTIME_UNAVAILABLE_REASON = (
    "PySide6 is not installed; install codex-usage-hud[desktop-overlay] "
    "to enable desktop work bubbles."
)


@dataclass(frozen=True, slots=True)
class HelperHealthDecision:
    """Decision returned from one helper health observation."""

    action: str
    exit_code: int | None = None
    restart_blocked_until: float = 0.0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeAvailabilityDecision:
    """Cached PySide availability result and its user-facing reason."""

    available: bool
    reason: str = ""


def probe_runtime_availability(
    *,
    cached: bool | None,
    probe: Callable[[], bool],
    unavailable_reason: str = "",
) -> RuntimeAvailabilityDecision:
    """Evaluate the injected PySide probe while preserving its cached result."""

    if cached is not None:
        return RuntimeAvailabilityDecision(bool(cached), str(unavailable_reason or ""))
    reason = str(unavailable_reason or "")
    try:
        available = bool(probe())
    except (ImportError, AttributeError, ValueError) as exc:
        available = False
        reason = str(exc)
    if not available and not reason:
        reason = DEFAULT_RUNTIME_UNAVAILABLE_REASON
    return RuntimeAvailabilityDecision(available, reason)


def next_keep_alive_seconds(
    *,
    closed: bool,
    enabled: bool,
    has_payload: bool,
    has_rest_reminder: bool,
    now_monotonic: float,
    last_state_write_at: float,
    keepalive_seconds: float,
) -> float | None:
    """Return the next state refresh deadline without touching overlay state."""

    if (
        closed
        or (not enabled and not has_rest_reminder)
        or (not has_payload and not has_rest_reminder)
    ):
        return None
    elapsed = float(now_monotonic) - float(last_state_write_at)
    return max(0.1, float(keepalive_seconds) - max(0.0, elapsed))


def route_system_action_commands(
    commands: Sequence[Mapping[str, object]],
    *,
    accepted_actions: set[str],
    expected_action_id: str,
) -> tuple[dict[str, object] | None, list[dict[str, object]], str | None]:
    """Classify system-action rows before the watcher applies side effects."""

    matched: dict[str, object] | None = None
    deferred: list[dict[str, object]] = []
    runtime_error: str | None = None
    for command in commands:
        action = str(command.get("action") or "").strip()
        if action == "runtimeError":
            runtime_error = str(
                command.get("message") or "PySide6 desktop overlay helper error"
            )
            continue
        if action not in accepted_actions:
            deferred.append(dict(command))
            continue
        action_id = str(command.get("actionId") or "").strip()
        if expected_action_id and action_id != expected_action_id:
            continue
        if matched is None:
            matched = dict(command)
    return matched, deferred, runtime_error


def evaluate_helper_health(
    *,
    process_exit_code: object | None,
    user_object_count: object | None,
    helper_started_at: float,
    last_heartbeat_at: float,
    now_monotonic: float,
    now_wall: float,
    heartbeat_timeout_seconds: float,
    max_user_objects: int,
    restart_backoff_seconds: float,
) -> HelperHealthDecision:
    """Evaluate one observed helper state without touching external resources.

    A non-``None`` process exit takes precedence over resource and heartbeat
    checks.  A clean exit is immediately restartable; a failed exit receives
    the existing bounded backoff.  Resource exhaustion and stale heartbeat
    observations request a restart while leaving termination/startup to the
    caller.
    """

    if process_exit_code is not None:
        try:
            exit_code = int(process_exit_code or 0)
        except (TypeError, ValueError, OverflowError):
            exit_code = 0
        return HelperHealthDecision(
            action=EXITED,
            exit_code=exit_code,
            restart_blocked_until=(
                0.0
                if exit_code == 0
                else float(now_monotonic) + max(0.0, float(restart_backoff_seconds))
            ),
            reason=(
                ""
                if exit_code == 0
                else f"PySide6 desktop overlay helper exited with code {exit_code}"
            ),
        )

    try:
        object_count = int(user_object_count) if user_object_count is not None else None
    except (TypeError, ValueError, OverflowError):
        object_count = None
    if object_count is not None and object_count >= int(max_user_objects):
        return HelperHealthDecision(
            action=RESTART,
            reason=f"user_objects={object_count}",
        )

    heartbeat_at = max(float(helper_started_at), float(last_heartbeat_at))
    if heartbeat_at <= 0.0:
        return HelperHealthDecision(action=HEALTHY)
    heartbeat_age = float(now_wall) - heartbeat_at
    if heartbeat_age < float(heartbeat_timeout_seconds):
        return HelperHealthDecision(action=HEALTHY)
    return HelperHealthDecision(
        action=RESTART,
        reason=f"heartbeat_age_seconds={heartbeat_age:.1f}",
    )


__all__ = [
    "DEFAULT_RUNTIME_UNAVAILABLE_REASON",
    "EXITED",
    "HEALTHY",
    "RESTART",
    "HelperHealthDecision",
    "RuntimeAvailabilityDecision",
    "evaluate_helper_health",
    "next_keep_alive_seconds",
    "probe_runtime_availability",
    "route_system_action_commands",
]
