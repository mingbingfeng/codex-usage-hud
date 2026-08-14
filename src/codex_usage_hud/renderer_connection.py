"""Renderer connection-health payload helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
import threading
import time

from .core.connection_health import ConnectionHealth


_LOGGER = logging.getLogger(__name__)


def diagnostics_light_payload(
    connection_health: Mapping[str, object],
    *,
    debug: bool,
    runtime_errors: list[object],
) -> dict[str, object]:
    domain = {
        "connectionHealth": dict(connection_health),
        "debug": bool(debug),
        "runtimeErrors": list(runtime_errors),
    }
    return {
        **domain,
        "payloadDomains": {"diagnostics": dict(domain)},
    }


class RendererConnectionManager:
    """Own lightweight health pushes, bounded probes, and follow healing."""

    def __init__(
        self,
        *,
        client: object,
        tracker_provider: Callable[[], object | None],
        wake: Callable[[], None],
        schedule_soft_reinstall: Callable[[], None],
        debug_enabled: Callable[[], bool],
        runtime_errors: Callable[[], list[object]],
        health: ConnectionHealth | None = None,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.tracker_provider = tracker_provider
        self.wake = wake
        self.schedule_soft_reinstall = schedule_soft_reinstall
        self.debug_enabled = debug_enabled
        self.runtime_errors = runtime_errors
        self.health = health or ConnectionHealth()
        self.wall_time = wall_time
        self._light_push_enabled = False
        self._connection_attempt_lock = threading.Lock()
        self._connection_attempt_generation = 0

    def enable_light_push(self) -> None:
        self._light_push_enabled = True

    def request_light(self) -> None:
        if self._light_push_enabled:
            try:
                self.push_light()
                return
            except Exception:
                pass
        self.wake()

    def _run_connection_attempt(self, operation: Callable[[], object]) -> bool:
        """Serialize probe/report/rebind calls so only one can touch CDP."""
        if not self._connection_attempt_lock.acquire(blocking=False):
            return False
        self._connection_attempt_generation += 1
        generation = self._connection_attempt_generation
        try:
            return generation == self._connection_attempt_generation and bool(operation())
        finally:
            self._connection_attempt_lock.release()

    def push_light(self) -> bool:
        gate = getattr(self.client, "update_gate_state", None)
        if callable(gate):
            try:
                allowed, _reason, _remaining = gate()
                if not allowed:
                    return False
            except Exception:
                pass
        payload = diagnostics_light_payload(
            self.health.to_payload(),
            debug=self.debug_enabled(),
            runtime_errors=self.runtime_errors(),
        )
        update_payload = getattr(self.client, "update_payload", None)
        if not callable(update_payload):
            return False
        try:
            return bool(update_payload(payload))
        except Exception as exc:
            _LOGGER.debug("connection_health_light_push_failed error=%s", exc)
            return False

    def follow_elapsed_ms(self, snapshot: object | None) -> int:
        tracker = self.tracker_provider()
        stuck = getattr(tracker, "follow_stuck_elapsed_ms", None)
        if stuck is not None:
            try:
                value = int(stuck)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        observed_at_ms = int(
            getattr(snapshot, "selection_observed_at_ms", 0) or 0
        )
        if observed_at_ms <= 0:
            return 0
        return max(0, int(self.wall_time() * 1000) - observed_at_ms)

    def follow_values(self, snapshot: object | None) -> tuple[str, str, int]:
        tracker = self.tracker_provider()
        follow_state = str(
            getattr(snapshot, "follow_state", None)
            or getattr(tracker, "follow_state", "")
            or ""
        )
        follow_reason = str(
            getattr(snapshot, "follow_reason", None)
            or getattr(tracker, "follow_reason", "")
            or ""
        )
        return follow_state, follow_reason, self.follow_elapsed_ms(snapshot)

    def sync_follow(self, snapshot: object | None) -> None:
        follow_state, follow_reason, elapsed_ms = self.follow_values(snapshot)
        self.health.observe_follow(
            follow_state=follow_state,
            follow_reason=follow_reason,
            follow_stuck_elapsed_ms=elapsed_ms,
        )

    def maybe_probe(self, snapshot: object | None, *, update_failures: int) -> bool:
        gate = getattr(self.client, "update_gate_state", None)
        if callable(gate):
            try:
                allowed, _reason, _remaining = gate()
                if not allowed:
                    return False
            except Exception:
                pass
        self.sync_follow(snapshot)
        follow_state, _, follow_elapsed_ms = self.follow_values(snapshot)
        if not self.health.should_probe(
            follow_state=follow_state,
            follow_elapsed_ms=follow_elapsed_ms,
            update_failures=update_failures,
        ):
            return False
        before = self._health_signature()
        probe = getattr(self.client, "probe_connection", None)
        ok = bool(callable(probe) and self._run_connection_attempt(probe))
        if ok:
            self.health.note_success("probe-ok")
            self.sync_follow(snapshot)
            _LOGGER.debug("connection_heartbeat ok")
        else:
            self.health.note_failure("probe-failed")
            _LOGGER.info(
                "connection_heartbeat failed status=%s error=%s",
                getattr(self.client, "last_status", ""),
                getattr(self.client, "last_error", ""),
            )
        if self._health_signature() != before:
            self.push_light()
        return True

    def maybe_heal(self, snapshot: object | None) -> bool:
        gate = getattr(self.client, "update_gate_state", None)
        if callable(gate):
            try:
                allowed, _reason, _remaining = gate()
                if not allowed:
                    return False
            except Exception:
                pass
        self.sync_follow(snapshot)
        tracker = self.tracker_provider()
        follow_state, follow_reason, follow_elapsed_ms = self.follow_values(snapshot)
        should, heal_reason = self.health.should_heal(
            follow_state=follow_state,
            follow_reason=follow_reason,
            follow_elapsed_ms=follow_elapsed_ms,
        )
        if not should:
            return False
        before = self._follow_snapshot(
            tracker,
            fallback={
                "followState": follow_state,
                "followReason": follow_reason,
                "newSession": follow_state == "new-session",
            },
        )
        self.health.note_healing(heal_reason)
        self.push_light()
        _LOGGER.info(
            "session_follow_heal start reason=%s follow_state=%s follow_reason=%s elapsed_ms=%s",
            heal_reason,
            follow_state,
            follow_reason,
            follow_elapsed_ms,
        )
        report = getattr(self.client, "report_active_session", None)
        rebind = getattr(self.client, "rebind_active_session_channel", None)
        rematerialize = getattr(tracker, "rematerialize_renderer_mapping", None)

        if (
            heal_reason == "stuck-pending"
            or follow_reason
            in {
                "awaiting-exact-mapping",
                "awaiting-persistence",
                "awaiting-canonical-id",
            }
        ) and callable(rematerialize):
            try:
                if rematerialize(force=True) and self._follow_advanced(tracker, before):
                    return self._finish_heal("l0_rematerialize", heal_reason)
            except Exception as exc:
                _LOGGER.info(
                    "session_follow_heal l0_rematerialize_failed reason=%s error=%s",
                    heal_reason,
                    exc,
                )

        if callable(report) and self._run_connection_attempt(
            lambda: report(f"self-heal:{heal_reason}")
        ):
            if self._follow_advanced(tracker, before):
                return self._finish_heal("l1_report", heal_reason)
            self.health.note_heal_no_progress("heal-no-progress")
            self.push_light()
            _LOGGER.info(
                "session_follow_heal l1_no_progress reason=%s before=%s after=%s",
                heal_reason,
                before,
                self._follow_snapshot(tracker),
            )
        if callable(rebind) and self._run_connection_attempt(rebind):
            if callable(report) and self._run_connection_attempt(
                lambda: report(f"self-heal-rebind:{heal_reason}")
            ):
                if self._follow_advanced(tracker, before):
                    return self._finish_heal("l2_rebind", heal_reason)
                self.health.note_heal_no_progress("heal-no-progress")
                self.push_light()
                _LOGGER.info(
                    "session_follow_heal l2_no_progress reason=%s",
                    heal_reason,
                )
        clear_cache = getattr(self.client, "_clear_target_cache", None)
        if callable(clear_cache):
            try:
                clear_cache(clear_script=True)
                self.schedule_soft_reinstall()
                self.health.note_failure("heal-failed")
                self.push_light()
                self.wake()
                _LOGGER.info(
                    "session_follow_heal l3_soft_reinstall scheduled reason=%s",
                    heal_reason,
                )
                return True
            except Exception as exc:
                _LOGGER.info(
                    "session_follow_heal l3_failed reason=%s error=%s",
                    heal_reason,
                    exc,
                )
        self.health.note_failure("heal-failed")
        self.push_light()
        return False

    def activity_wake(self, snapshot: object | None, *, reason: str) -> bool:
        gate = getattr(self.client, "update_gate_state", None)
        if callable(gate):
            try:
                allowed, _reason, _remaining = gate()
                if not allowed:
                    return False
            except Exception:
                pass
        tracker = self.tracker_provider()
        follow_state, follow_reason, _ = self.follow_values(snapshot)
        if follow_state not in {"new-session", "pending"}:
            return False
        before = self._follow_snapshot(
            tracker,
            fallback={"followState": follow_state},
        )
        if follow_reason in {
            "awaiting-exact-mapping",
            "awaiting-persistence",
            "awaiting-canonical-id",
        } or str(reason).startswith("session-map"):
            rematerialize = getattr(tracker, "rematerialize_renderer_mapping", None)
            if callable(rematerialize):
                try:
                    if rematerialize(force=True):
                        after = self._follow_snapshot(tracker)
                        if self._follow_advanced(
                            tracker,
                            before,
                            after=after,
                            fallback_to_renderer_state=False,
                        ) or str(after.get("followState") or "") == "confirmed":
                            self.health.note_heal_success()
                            self.push_light()
                            self.wake()
                            _LOGGER.info(
                                "session_follow_activity_wake rematerialize ok reason=%s",
                                reason,
                            )
                            return True
                except Exception as exc:
                    _LOGGER.debug(
                        "session_follow_activity_wake rematerialize_failed reason=%s error=%s",
                        reason,
                        exc,
                    )

        report = getattr(self.client, "report_active_session", None)
        if not callable(report):
            return False
        ok = self._run_connection_attempt(
            lambda: report(f"activity-wake:{reason}")
        )
        after = self._follow_snapshot(tracker)
        advanced = self._follow_advanced(
            tracker,
            before,
            after=after,
            fallback_to_renderer_state=False,
        )
        _LOGGER.info(
            "session_follow_activity_wake reason=%s ok=%s advanced=%s follow_state=%s",
            reason,
            ok,
            advanced,
            getattr(tracker, "follow_state", follow_state)
            if tracker is not None
            else follow_state,
        )
        if advanced:
            self.health.note_heal_success()
            self.push_light()
            self.wake()
            return True
        self.sync_follow(snapshot)
        if self.health.state != "ok":
            self.push_light()
        return False

    def _finish_heal(self, stage: str, reason: str) -> bool:
        self.health.note_heal_success()
        self.push_light()
        self.wake()
        _LOGGER.info("session_follow_heal %s ok reason=%s", stage, reason)
        return True

    def _health_signature(self) -> tuple[str, str, bool]:
        return (
            self.health.state,
            self.health.reason,
            self.health.channel_available,
        )

    @staticmethod
    def _follow_snapshot(
        tracker: object | None,
        *,
        fallback: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        snapshot = getattr(tracker, "follow_snapshot", None)
        if callable(snapshot):
            return dict(snapshot())
        return dict(fallback or {})

    def _follow_advanced(
        self,
        tracker: object | None,
        before: Mapping[str, object],
        *,
        after: Mapping[str, object] | None = None,
        fallback_to_renderer_state: bool = True,
    ) -> bool:
        current = dict(after) if after is not None else self._follow_snapshot(tracker)
        progressed = getattr(
            type(tracker) if tracker is not None else object,
            "follow_progressed",
            None,
        )
        if callable(progressed):
            return bool(progressed(before, current))
        if fallback_to_renderer_state and tracker is not None:
            return not bool(getattr(tracker, "renderer_new_session", False))
        return False


__all__ = ["RendererConnectionManager", "diagnostics_light_payload"]
