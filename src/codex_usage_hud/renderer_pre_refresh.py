"""Command and settings work that precedes renderer snapshot refreshes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

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


class RendererPreRefreshExecutor:
    """Apply command/background/settings-file work before snapshot execution."""

    _BACKGROUND_QUERY_ACTIONS = frozenset(
        {
            "openBackgroundUsage",
            "openBackgroundUsageFromInsights",
            "backgroundUsageQuery",
            "backgroundUsageDetail",
        }
    )

    def __init__(
        self,
        state: RendererLoopState,
        ports: RendererPreRefreshPorts,
    ) -> None:
        self.state = state
        self.ports = ports

    def apply(self, inputs: RendererTickInputs) -> None:
        self.apply_settings_command(inputs)
        self.apply_background_usage_change(inputs)
        self.apply_partial_settings_file_change(inputs)

    def apply_settings_command(self, inputs: RendererTickInputs) -> None:
        if not inputs.command:
            return
        previous_config = self.ports.current_config()
        action = str(inputs.command.get("action") or "").strip()
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
