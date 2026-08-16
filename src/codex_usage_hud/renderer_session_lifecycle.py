"""Owned resources and startup feedback for one renderer HUD session."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging

from .renderer_event_loop import RendererLoopState


# Keep the established diagnostic logger name while moving its lifecycle owner.
_LOGGER = logging.getLogger("codex_usage_hud.renderer_runtime")


@dataclass(slots=True)
class RendererSessionResources:
    """Own resources created for one renderer session and close them once."""

    context: object | None = None
    overlay: object | None = None
    update_manager: object | None = None
    client: object | None = None
    runtime_event_unsubscribe: Callable[[], None] | None = None
    bridge_callbacks: object | None = None
    bridge: object | None = None
    command_pump: object | None = None
    file_events: object | None = None
    active_work_pump: object | None = None
    pre_refresh_executor: object | None = None
    _closed: bool = field(default=False, init=False, repr=False)

    def release_overlay_for_handoff(self) -> object | None:
        """Detach the desktop overlay before a daemon-triggered session swap.

        The overlay helper is intentionally independent from the Renderer/CDP
        session.  A Codex process replacement should therefore close the
        renderer resources while allowing the existing work bubbles to remain
        alive until the replacement session is attached.
        """
        overlay = self.overlay
        self.overlay = None
        return overlay

    def close(self) -> None:
        """Release optional resources in strict reverse construction order."""
        if self._closed:
            return
        self._closed = True
        for field_name, close_name in (
            ("pre_refresh_executor", "close"),
            ("active_work_pump", "close"),
            ("file_events", "close"),
            ("command_pump", "close"),
            ("bridge", "close"),
            ("bridge_callbacks", "disconnect_tracker"),
            ("runtime_event_unsubscribe", "__call__"),
            ("client", "close"),
            ("update_manager", "close"),
            ("overlay", "close"),
            ("context", "close"),
        ):
            resource = getattr(self, field_name)
            if resource is None:
                continue
            try:
                close = getattr(resource, close_name, None)
                if callable(close):
                    close()
            except Exception:
                _LOGGER.exception(
                    "renderer_session_close_failed resource=%s",
                    field_name,
                )
            finally:
                setattr(self, field_name, None)


@dataclass(slots=True)
class RendererSessionLoopControls:
    """Own loop-level lifecycle signals and scheduled response retries."""

    state: RendererLoopState
    monotonic: Callable[[], float]
    response_pending: Callable[[dict[str, object]], bool]
    response_retry_delay: Callable[[int], float | None]
    exit_event: object
    restart_event: object
    overlay: object
    daemon_restart_result: int
    restart_codex_event: object | None = None
    restart_codex_result: int = 0
    daemon_manager: object | None = None
    daemon_failure_exception: type[Exception] = RuntimeError
    unavailable_result: int = 0
    connection_manager: object | None = None

    def schedule_soft_reinstall(self) -> None:
        self.state.soft_reinstall_pending = True

    def reset_background_retry(self) -> None:
        self.state.background_usage_response_retry_attempts = 0
        self.state.background_usage_response_retry_not_before = 0.0

    def schedule_background_retry(self) -> None:
        if not self.response_pending(self.state.settings_command_status):
            return
        next_attempt = self.state.background_usage_response_retry_attempts + 1
        delay = self.response_retry_delay(next_attempt)
        if delay is None:
            self.reset_background_retry()
            return
        self.state.background_usage_response_retry_attempts = next_attempt
        self.state.background_usage_response_retry_not_before = (
            self.monotonic() + delay
        )

    def current_session(self, path_key: Callable[[object], str]) -> str | None:
        session_path = getattr(self.state.latest_snapshot, "session_path", None)
        return None if session_path is None else path_key(session_path)

    def exit_requested(self) -> bool:
        requested = bool(self.exit_event.is_set())
        if requested:
            _LOGGER.info("renderer_hud_exit_requested")
        return requested

    def restart_requested(self) -> bool:
        requested = bool(self.restart_event.is_set()) or bool(
            self.restart_codex_event is not None
            and self.restart_codex_event.is_set()
        )
        if requested:
            _LOGGER.info(
                "renderer_hud_restart_requested kind=%s",
                "codex" if self.restart_codex_event is not None and self.restart_codex_event.is_set() else "hud",
            )
        return requested

    def restart_result(self) -> int:
        if self.restart_codex_event is not None and self.restart_codex_event.is_set():
            return self.restart_codex_result
        return self.daemon_restart_result

    def daemon_tick(self) -> int | None:
        manager = self.daemon_manager
        if manager is None or self.monotonic() < self.state.next_daemon_check_at:
            return None
        try:
            if not manager.codex_is_running():
                _LOGGER.info("daemon_codex_exited")
                return self.daemon_restart_result
            self.state.next_daemon_check_at = (
                self.monotonic() + manager.poll_seconds
            )
        except self.daemon_failure_exception as exc:
            _LOGGER.exception("daemon_watchdog_failed fallback=%s", exc)
            return self.unavailable_result
        return None

    def keep_overlay_alive(self) -> None:
        keep_alive = getattr(self.overlay, "keep_alive", None)
        if callable(keep_alive):
            keep_alive()

    def after_iteration(self, snapshot: object | None) -> None:
        manager = self.connection_manager
        if manager is None:
            return
        wake_reason = str(self.state.activity_wake_pending or "")
        if wake_reason:
            self.state.activity_wake_pending = ""
            manager.activity_wake(snapshot, reason=wake_reason)
        manager.maybe_heal(snapshot)
        manager.maybe_probe(snapshot, update_failures=self.state.failures)


class RendererStartupFeedback:
    """Own startup-domain payloads and active-session bootstrap sequencing."""

    def __init__(
        self,
        client: object,
        wake_event: object,
        *,
        bootstrap_wait_seconds: float,
    ) -> None:
        self.client = client
        self.wake_event = wake_event
        self.bootstrap_wait_seconds = max(0.0, float(bootstrap_wait_seconds))

    @staticmethod
    def payload(
        *,
        step: str,
        detail: str,
        progress: int,
    ) -> dict[str, object]:
        return {
            "payloadDomains": {
                "startup": {
                    "step": step,
                    "title": "正在启动 Codex HUD",
                    "detail": detail,
                    "progress": max(0, min(100, int(progress))),
                }
            }
        }

    def update(
        self,
        *,
        step: str,
        detail: str,
        progress: int,
    ) -> bool:
        show_startup = getattr(self.client, "show_startup", None)
        if not callable(show_startup):
            return False
        return bool(
            show_startup(self.payload(step=step, detail=detail, progress=progress))
        )

    def bootstrap(
        self,
        *,
        step: str,
        detail: str,
        progress: int,
    ) -> bool:
        bootstrap = getattr(self.client, "bootstrap_active_session", None)
        if not callable(bootstrap):
            return False
        clear = getattr(self.wake_event, "clear", None)
        if callable(clear):
            clear()
        startup_payload = self.payload(
            step=step,
            detail=detail,
            progress=progress,
        )
        try:
            bootstrapped = bool(bootstrap(startup_payload=startup_payload))
        except TypeError:
            bootstrapped = bool(bootstrap())
        metrics = dict(getattr(self.client, "last_bootstrap_metrics", {}) or {})
        _LOGGER.info(
            "renderer_active_session_bootstrap ok=%s step=%s progress=%s total_ms=%s failure_stage=%s",
            bootstrapped,
            step,
            progress,
            metrics.get("totalMs", "-"),
            metrics.get("failureStage", ""),
        )
        wait = getattr(self.wake_event, "wait", None)
        if bootstrapped and callable(wait):
            wait(self.bootstrap_wait_seconds)
        return bootstrapped

    def progress(self, stage: str) -> None:
        stages = {
            "reading_session": (
                "第 3 步，共 4 步",
                "正在识别当前打开的会话…",
                62,
            ),
            "showing_hud": (
                "第 4 步，共 4 步",
                "会话信息已就绪，正在显示用量与预算…",
                88,
            ),
        }
        step, detail, progress = stages.get(
            str(stage or ""),
            ("正在启动", "正在准备 HUD…", 40),
        )
        self.update(step=step, detail=detail, progress=progress)


__all__ = [
    "RendererSessionLoopControls",
    "RendererSessionResources",
    "RendererStartupFeedback",
]
