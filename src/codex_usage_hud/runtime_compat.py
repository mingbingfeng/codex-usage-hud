"""Legacy runtime symbol resolution kept outside the composition root."""

from __future__ import annotations

from dataclasses import fields
from typing import Mapping

from . import (
    active_work,
    codex_app_runtime,
    daemon_runtime,
    desktop_overlay,
    desktop_overlay_setup,
    loading_feedback,
    overlay_commands,
    overlay_ipc,
    overlay_projection,
    overlay_runtime,
    renderer_file_events,
    renderer_runtime_policies,
    renderer_runtime,
    renderer_session_ports,
    renderer_startup,
    runtime_commands,
    runtime_config,
    runtime_context,
    runtime_diagnostics,
    runtime_paths,
    runtime_policies,
    runtime_settings,
    runtime_snapshot_service,
    runtime_usage,
    session_cleanup_runtime,
    session_snapshots,
    snapshot_builder,
    usage_cache,
    usage_insights,
)
from .core.calculator import UsageCalculator
from .core.parser import CostEstimator
from .platforms import cdp_probe
from .runtime_ports import RuntimeServices
from .ui import renderer_domains


_OWNERS = (
    active_work,
    codex_app_runtime,
    daemon_runtime,
    desktop_overlay,
    desktop_overlay_setup,
    loading_feedback,
    overlay_commands,
    overlay_ipc,
    overlay_projection,
    overlay_runtime,
    renderer_domains,
    renderer_file_events,
    renderer_runtime_policies,
    renderer_session_ports,
    renderer_runtime,
    renderer_startup,
    runtime_commands,
    runtime_config,
    runtime_context,
    runtime_diagnostics,
    runtime_paths,
    runtime_policies,
    runtime_settings,
    runtime_snapshot_service,
    runtime_usage,
    session_cleanup_runtime,
    session_snapshots,
    snapshot_builder,
    usage_cache,
    usage_insights,
    cdp_probe,
)

_SPECIAL = {
    "CostEstimator": CostEstimator,
    "RuntimeServices": RuntimeServices,
    "UsageCalculator": UsageCalculator,
    "_assign_fresh_renderer_cdp_port": renderer_startup.assign_fresh_cdp_port,
    "_background_usage_query_payload_with_preview": runtime_commands._query_with_preview,
    "_background_usage_response_retry_delay_seconds": runtime_settings.background_usage_retry_delay,
    "_changed_user_config_keys": runtime_settings.changed_config_keys,
    "_handle_work_overlay_command": overlay_runtime._handle_work_overlay_command,
    "_handle_work_overlay_commands": overlay_runtime._handle_work_overlay_commands,
    "_partial_domains_for_changed_user_config": runtime_settings.partial_domains_for_changed_config,
    "_partial_domains_for_settings_command": runtime_settings.partial_domains_for_command,
    "_refresh_latest_snapshot_for_partial_settings_command": runtime_commands.refresh_latest_snapshot_for_partial_settings_command,
    "_work_overlay_command_path": overlay_ipc.command_path,
    "_work_overlay_heartbeat_path": overlay_ipc.heartbeat_path,
    "budget_warnings": runtime_policies.budget_warning_messages,
    "current_budget_windows": runtime_policies.budget_windows,
}


def resolve(name: str) -> object:
    """Resolve a historical coordinator export from its current owner."""
    value = _SPECIAL.get(name)
    if value is not None:
        return value
    candidates = (name, name.lstrip("_"))
    for owner in _OWNERS:
        for candidate in candidates:
            try:
                return getattr(owner, candidate)
            except AttributeError:
                continue
    raise AttributeError(f"runtime_orchestration has no attribute {name!r}")


def session_port_bindings(root_values: Mapping[str, object]) -> dict[str, object]:
    """Materialize declared renderer ports while preferring root callbacks."""
    values: dict[str, object] = {}
    for field in fields(renderer_session_ports.RendererSessionPorts):
        values[field.name] = (
            root_values[field.name]
            if field.name in root_values
            else resolve(field.name)
        )
    return values
