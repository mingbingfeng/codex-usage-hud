"""Command and settings work that precedes renderer snapshot refreshes."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import logging
from threading import Lock, Thread, current_thread

from .renderer_event_loop import RendererLoopState, RendererTickInputs


_LOGGER = logging.getLogger("codex_usage_hud.renderer_event_loop")

@dataclass(frozen=True, slots=True)
class RendererPreRefreshPorts:
    """Command and settings work that precedes snapshot refresh execution."""

    current_config: Callable[[], object]
    execute_command: Callable[[dict[str, object]], dict[str, object]]
    update_status: Callable[[], dict[str, object]]
    reset_background_retry: Callable[[], None]
    renderer_only_status: Callable[[str], dict[str, object]]
    partial_domains_for_command: Callable[
        [dict[str, object], object, object],
        set[str] | None,
    ]
    refresh_latest_snapshot: Callable[
        [dict[str, object], object, object, object],
        None,
    ]
    refresh_usage_insights: Callable[[], None]
    overlay_configure: Callable[[], None]
    overlay_update: Callable[[list[object]], None]
    items_with_background_usage: Callable[[list[object]], list[object]]
    settings_store: object | None
    apply_config: Callable[[object, object], None]
    changed_config_keys: Callable[[object, object], set[str]]
    partial_domains_for_changes: Callable[[set[str]], set[str] | None]
    request_usage_insights_refresh: Callable[[], None] | None = None
    wake: Callable[[], None] | None = None


class RendererPreRefreshExecutor:
    """Apply command/background/settings-file work before snapshot execution."""

    _CODEX_CLI_LAUNCH_ACTION = "codexCliLaunch"
    _CODEX_CLI_LAUNCH_PENDING_ACTION = "codexCliLaunchPending"
    _BACKGROUND_QUERY_ACTIONS = frozenset(
        {
            "openBackgroundUsage",
            "openBackgroundUsageFromInsights",
            "backgroundUsageQuery",
            "backgroundUsageDetail",
        }
    )
    _REQUEST_ROWS_LOAD_MORE_ACTION = "loadMoreRequestRows"
    _REQUEST_ROWS_PAGE_SIZE = 30

    def __init__(
        self,
        state: RendererLoopState,
        ports: RendererPreRefreshPorts,
    ) -> None:
        self.state = state
        self.ports = ports
        self._async_lock = Lock()
        self._closed = False
        self._codex_cli_launch_request_id = ""
        self._codex_cli_launch_thread: Thread | None = None
        self._codex_cli_launch_results: deque[tuple[str, dict[str, object]]] = deque()

    def close(self) -> None:
        """Stop accepting launch results and bound the worker during shutdown."""
        with self._async_lock:
            self._closed = True
            thread = self._codex_cli_launch_thread
        if thread is not None and thread is not current_thread() and thread.is_alive():
            thread.join(timeout=2.0)
        with self._async_lock:
            self._codex_cli_launch_results.clear()
            self._codex_cli_launch_request_id = ""
            self._codex_cli_launch_thread = None

    def apply(self, inputs: RendererTickInputs) -> None:
        self.apply_async_command_results(inputs)
        self.apply_usage_insights_refresh(inputs)
        self.apply_settings_command(inputs)
        self.apply_background_usage_change(inputs)
        self.apply_partial_settings_file_change(inputs)

    def apply_async_command_results(self, inputs: RendererTickInputs) -> None:
        with self._async_lock:
            results = list(self._codex_cli_launch_results)
            self._codex_cli_launch_results.clear()
        for request_id, status in results:
            with self._async_lock:
                if request_id != self._codex_cli_launch_request_id:
                    continue
                self._codex_cli_launch_request_id = ""
            self.state.settings_command_status = dict(status)
            inputs.update_state = self.ports.update_status()
            self._request_settings_domain(inputs)

    @staticmethod
    def _command_status(
        *,
        action: str,
        request_id: str,
        message: str,
        kind: str = "",
    ) -> dict[str, object]:
        return {
            "action": action,
            "requestId": request_id,
            "message": message,
            "kind": kind,
            "restartVisible": False,
        }

    def _request_settings_domain(self, inputs: RendererTickInputs) -> None:
        if self._can_replace_snapshot_with_domains(inputs, {"settings"}):
            inputs.event_refresh_request.snapshot = False
        inputs.event_refresh_request.request_domains("settings", force_fast=True)

    def _start_codex_cli_launch(
        self,
        command: dict[str, object],
        inputs: RendererTickInputs,
    ) -> None:
        request_id = str(
            command.get("requestId") or command.get("id") or "codex-cli-launch"
        ).strip()
        with self._async_lock:
            if self._closed:
                self.state.settings_command_status = self._command_status(
                    action=self._CODEX_CLI_LAUNCH_ACTION,
                    request_id=request_id,
                    message="Codex CLI 启动器已停止。",
                    kind="error",
                )
                self._request_settings_domain(inputs)
                return
            if self._codex_cli_launch_request_id:
                self.state.settings_command_status = self._command_status(
                    action=self._CODEX_CLI_LAUNCH_ACTION,
                    request_id=request_id,
                    message="上一次 Codex CLI 启动仍在进行，请稍候。",
                    kind="error",
                )
                self._request_settings_domain(inputs)
                return
            self._codex_cli_launch_request_id = request_id
            thread = Thread(
                target=self._run_codex_cli_launch,
                args=(dict(command), request_id),
                name="codex-usage-hud-cli-launch",
                daemon=True,
            )
            self._codex_cli_launch_thread = thread
        self.state.settings_command_status = self._command_status(
            action=self._CODEX_CLI_LAUNCH_PENDING_ACTION,
            request_id=request_id,
            message="正在打开终端并启动 Codex CLI...",
        )
        inputs.update_state = self.ports.update_status()
        self._request_settings_domain(inputs)
        _LOGGER.info("renderer_codex_cli_launch_started request_id=%s", request_id)
        try:
            thread.start()
        except Exception as exc:
            _LOGGER.exception("renderer_codex_cli_launch_thread_start_failed")
            with self._async_lock:
                self._codex_cli_launch_request_id = ""
                self._codex_cli_launch_thread = None
            self.state.settings_command_status = self._command_status(
                action=self._CODEX_CLI_LAUNCH_ACTION,
                request_id=request_id,
                message=f"Codex CLI 启动失败：{exc}",
                kind="error",
            )
            self._request_settings_domain(inputs)

    def _run_codex_cli_launch(
        self,
        command: dict[str, object],
        request_id: str,
    ) -> None:
        try:
            status = dict(self.ports.execute_command(command))
        except Exception as exc:
            _LOGGER.exception(
                "renderer_codex_cli_launch_failed request_id=%s",
                request_id,
            )
            status = self._command_status(
                action=self._CODEX_CLI_LAUNCH_ACTION,
                request_id=request_id,
                message=f"Codex CLI 启动失败：{exc}",
                kind="error",
            )
        status.setdefault("action", self._CODEX_CLI_LAUNCH_ACTION)
        status.setdefault("requestId", request_id)
        with self._async_lock:
            if self._closed:
                return
            self._codex_cli_launch_results.append((request_id, status))
        wake = self.ports.wake
        if callable(wake):
            wake()
        _LOGGER.info(
            "renderer_codex_cli_launch_finished request_id=%s kind=%s",
            request_id,
            str(status.get("kind") or "ok"),
        )

    def apply_usage_insights_refresh(self, inputs: RendererTickInputs) -> None:
        if not inputs.event_refresh_request.usage_insights_refresh:
            return
        request = self.ports.request_usage_insights_refresh
        if callable(request):
            request()

    def apply_settings_command(self, inputs: RendererTickInputs) -> None:
        if not inputs.command:
            return
        action = str(inputs.command.get("action") or "").strip()
        if action == self._REQUEST_ROWS_LOAD_MORE_ACTION:
            self.state.request_rows_limit += self._REQUEST_ROWS_PAGE_SIZE
            self.state.settings_command_status = {}
            return
        if action == self._CODEX_CLI_LAUNCH_ACTION:
            self._start_codex_cli_launch(dict(inputs.command), inputs)
            return
        previous_config = self.ports.current_config()
        if action in self._BACKGROUND_QUERY_ACTIONS:
            self.ports.reset_background_retry()
        self.state.settings_command_status = self.ports.execute_command(inputs.command)
        inputs.update_state = self.ports.update_status()
        mode_switch = str(
            self.state.settings_command_status.get("switchMode") or ""
        ).strip()
        if mode_switch and mode_switch != "renderer":
            _LOGGER.info("renderer_hud_legacy_switch_ignored mode=%s", mode_switch)
            self.state.settings_command_status = self.ports.renderer_only_status(
                "Renderer-only 版本不再切换到 Qt/Tk。"
            )
        current_config = self.ports.current_config()
        partial_domains = self.ports.partial_domains_for_command(
            inputs.command,
            previous_config,
            current_config,
        )
        if not self._can_replace_snapshot_with_domains(inputs, partial_domains):
            return
        if self.state.latest_snapshot is not None:
            self.ports.refresh_latest_snapshot(
                inputs.command,
                self.state.latest_snapshot,
                previous_config,
                current_config,
            )
        inputs.event_refresh_request.snapshot = False
        inputs.event_refresh_request.request_domains(
            *sorted(partial_domains or set()),
            force_fast=True,
        )

    def apply_background_usage_change(self, inputs: RendererTickInputs) -> None:
        if not inputs.event_refresh_request.background_usage:
            return
        if any(
            str(getattr(event, "type", "") or "")
            == "background_usage_changed"
            for event in inputs.runtime_events
        ):
            self.ports.refresh_usage_insights()
        session_items = (
            list(self.state.latest_snapshot.active_work_items)
            if self.state.latest_snapshot is not None
            else []
        )
        self.ports.overlay_configure()
        self.ports.overlay_update(
            self.ports.items_with_background_usage(session_items)
        )
        if not self.state.activity_wake_pending:
            self.state.activity_wake_pending = "background-usage"

    def apply_partial_settings_file_change(
        self,
        inputs: RendererTickInputs,
    ) -> None:
        event_types = {
            str(getattr(event, "type", "") or "")
            for event in inputs.runtime_events
        }
        if (
            self.state.latest_snapshot is None
            or inputs.command
            or not inputs.event_refresh_request.snapshot
            or inputs.active_session_wakeup
            or inputs.event_refresh_request.active_session
            or inputs.event_refresh_request.diagnostics
            or (
                inputs.file_change_reasons
                and inputs.file_change_reasons != {"settings"}
            )
            or event_types - {"settings_changed"}
        ):
            return
        load = getattr(self.ports.settings_store, "load", None)
        mtime_fn = getattr(self.ports.settings_store, "mtime", None)
        if not callable(load):
            return
        previous_config = self.ports.current_config()
        next_config = load()
        mtime = mtime_fn() if callable(mtime_fn) else None
        self.ports.apply_config(next_config, mtime)
        changed_keys = self.ports.changed_config_keys(
            previous_config,
            next_config,
        )
        partial_domains = self.ports.partial_domains_for_changes(changed_keys)
        if partial_domains is None:
            return
        self.ports.refresh_latest_snapshot(
            {"action": "save"},
            self.state.latest_snapshot,
            previous_config,
            next_config,
        )
        inputs.event_refresh_request.snapshot = False
        inputs.event_refresh_request.request_domains(
            *sorted(partial_domains),
            force_fast=True,
        )

    @staticmethod
    def _can_replace_snapshot_with_domains(
        inputs: RendererTickInputs,
        partial_domains: set[str] | None,
    ) -> bool:
        return bool(
            partial_domains
            and inputs.event_refresh_request.snapshot
            and not inputs.file_change_reasons
            and not inputs.active_session_wakeup
            and not inputs.event_refresh_request.active_session
            and not inputs.event_refresh_request.diagnostics
        )

__all__ = ["RendererPreRefreshExecutor", "RendererPreRefreshPorts"]
