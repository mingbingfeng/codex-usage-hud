"""Renderer runtime command handlers with explicitly supplied services."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from threading import Event
from typing import Any
import uuid

from . import runtime_settings
from . import runtime_config
from . import __version__
from .config import (
    DEFAULT_WORK_OVERLAY_MAX_ITEMS,
    UserConfig,
    dismiss_warning_for_today,
    fetch_model_prices,
)
from .desktop_overlay import DesktopWorkOverlay
from .desktop_overlay_setup import (
    _desktop_overlay_dependency_status,
    _pyside6_version,
    _set_force_desktop_overlay_missing,
    _start_desktop_overlay_install,
)
from .overlay_runtime import _handle_work_overlay_command
from .overlay_projection import _work_overlay_screen_max_items
from .platforms import SessionSwitchController
from .runtime_context import RuntimeContext
from .runtime_snapshot_service import _apply_pre_send_pricing
from .runtime_policies import budget_warning_messages
from .updater import AutoUpdateManager, check_for_update, download_update_asset, launch_installer


def _renderer_settings_status(
    message: str,
    *,
    kind: str = "info",
) -> dict[str, object]:
    return runtime_settings.settings_status(message, kind=kind)


def refresh_latest_snapshot_for_partial_settings_command(
    command: Mapping[str, Any],
    *,
    snapshot: object,
    context: object,
    previous_config: UserConfig,
    current_config: UserConfig,
) -> None:
    """Update only fields whose settings-domain payload is being pushed."""
    action = str(command.get("action") or "").strip()
    changed_keys = runtime_settings.changed_config_keys(
        previous_config, current_config
    )
    if action == "fetchPrices" or (
        action == "save"
        and changed_keys
        and changed_keys.issubset(runtime_settings.PRICING_KEYS)
    ):
        snapshot.estimate_base = _apply_pre_send_pricing(
            context, snapshot, snapshot.estimate_base
        )
    if action == "save" and changed_keys & runtime_settings.BUDGET_KEYS:
        raw_week_cost_usd = max(
            0.0,
            float(snapshot.week_cost_usd) - float(snapshot.week_adjustment_usd or 0.0),
        )
        week_adjustment_usd = max(
            0.0, float(current_config.weekly_adjustment_usd)
        )
        snapshot.week_adjustment_usd = week_adjustment_usd
        snapshot.week_cost_usd = round(raw_week_cost_usd + week_adjustment_usd, 6)
        snapshot.daily_limit_usd = max(0.0, float(current_config.daily_budget_usd))
        snapshot.weekly_limit_usd = max(0.0, float(current_config.weekly_budget_usd))
        snapshot.budget_warnings = budget_warning_messages(
            snapshot.today_cost_usd,
            snapshot.week_cost_usd,
            snapshot.daily_limit_usd,
            snapshot.weekly_limit_usd,
            list(current_config.budget_thresholds),
        )

_LOGGER = logging.getLogger(__name__)
UNHANDLED = object()


@dataclass(frozen=True, slots=True)
class RuntimeCommandPorts:
    background_usage: object | None = None
    cleanup_worker: object | None = None
    insights_worker: object | None = None
    insights_payload: Mapping[str, object] | None = None
    activate_session: Callable[[Mapping[str, object]], object | None] | None = None


@dataclass(frozen=True, slots=True)
class GeneralCommandPorts:
    load_config: Callable[[], Any]
    save_config: Callable[[Any], None]
    fetch_prices: Callable[[str], Mapping[str, Any]]
    rest_reminder: object | None
    update_manager: object | None
    work_overlay: object | None
    request_restart: Callable[[], None]
    request_exit: Callable[[], None]
    check_update: Callable[[], object]
    install_update: Callable[[object], None]
    overlay_status: Callable[[], Mapping[str, object]]
    start_overlay_install: Callable[[], bool]
    clear_forced_missing: Callable[[], None]
    forced_missing_with_real_install: Callable[[], bool]
    pyside_version: Callable[[], str]
    default_overlay_limit: Callable[[], int]
    dismiss_warnings_today: Callable[[], bool]


def _status(message: str, *, kind: str = "") -> dict[str, object]:
    return runtime_settings.settings_status(message, kind=kind)


def correlate_status(
    status: dict[str, object], command: Mapping[str, object]
) -> dict[str, object]:
    status.setdefault("requestId", str(command.get("requestId") or command.get("id") or ""))
    status.setdefault("action", str(command.get("action") or ""))
    return status


def dispatch_command(
    command: Mapping[str, Any],
    runtime_ports: RuntimeCommandPorts,
    general_ports: GeneralCommandPorts,
) -> dict[str, object]:
    for handler in (
        handle_cleanup_command,
        handle_insights_command,
        handle_background_command,
    ):
        handled = handler(command, runtime_ports)
        if handled is not UNHANDLED:
            return correlate_status(handled, command)
    return correlate_status(handle_general_command(command, general_ports), command)


def _query_with_preview(
    runtime: object,
    *,
    range_key: str,
    feature: str,
    model: str,
    event_id: str,
) -> dict[str, object]:
    query = getattr(runtime, "query", None)
    if not callable(query):
        raise RuntimeError("用量总览当前不可用。")
    raw_payload = query(
        range_key=range_key,
        feature=feature,
        model=model,
        event_id=event_id,
    )
    if not isinstance(raw_payload, Mapping):
        raise RuntimeError("后台用量查询返回了无效数据。")
    payload = dict(raw_payload)
    selected_event_id = str(payload.get("selectedEventId") or "").strip()
    selected_detail: dict[str, object] | None = None
    detail = getattr(runtime, "detail", None)
    if selected_event_id and callable(detail):
        try:
            raw_detail = detail(selected_event_id)
        except Exception as exc:
            _LOGGER.debug(
                "background_usage_preview_failed event_id=%s error=%s",
                selected_event_id,
                exc,
            )
        else:
            if isinstance(raw_detail, Mapping):
                selected_detail = dict(raw_detail)
                prompt = str(selected_detail.pop("prompt", "") or "")
                selected_detail["hasPrompt"] = bool(prompt)
    payload["selectedDetail"] = selected_detail
    return payload


def handle_background_command(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object] | object:
    action = str(command.get("action") or "").strip()
    if action not in {
        "backgroundUsageQuery",
        "backgroundUsageDetail",
        "openBackgroundUsage",
        "openBackgroundUsageFromInsights",
    }:
        return UNHANDLED
    request_id = str(command.get("requestId") or command.get("id") or "").strip()
    runtime = ports.background_usage
    try:
        if action == "backgroundUsageQuery":
            raw_filters = command.get("filters")
            filters = raw_filters if isinstance(raw_filters, Mapping) else {}
            payload = _query_with_preview(
                runtime,
                range_key=str(filters.get("range") or "today"),
                feature=str(filters.get("feature") or ""),
                model=str(filters.get("model") or ""),
                event_id=str(filters.get("eventId") or ""),
            )
            return runtime_settings.background_usage_response_status(
                "query", request_id, payload=payload
            )
        event_id = str(command.get("eventId") or "").strip()
        if action == "backgroundUsageDetail":
            detail = getattr(runtime, "detail", None)
            if not callable(detail):
                return runtime_settings.background_usage_response_status(
                    "detail",
                    request_id,
                    event_id=event_id,
                    error="用量总览当前不可用。",
                )
            if command.get("markViewed") is True:
                confirm = getattr(runtime, "confirm", None)
                if callable(confirm):
                    confirm(event_id)
            payload = detail(event_id) if event_id else None
            return runtime_settings.background_usage_response_status(
                "detail",
                request_id,
                payload=payload,
                event_id=event_id,
                error="" if payload is not None else "后台用量事件不存在。",
            )
        if event_id:
            confirm = getattr(runtime, "confirm", None)
            if callable(confirm):
                confirm(event_id)
        range_key = "today"
        range_for_event = getattr(runtime, "range_for_event", None)
        if event_id and callable(range_for_event):
            candidate = str(range_for_event(event_id) or "today").strip().lower()
            if candidate in {"today", "7d", "30d", "all"}:
                range_key = candidate
        payload = _query_with_preview(
            runtime,
            range_key=range_key,
            feature="",
            model="",
            event_id=event_id,
        )
        return runtime_settings.background_usage_response_status(
            "open", request_id, payload=payload, event_id=event_id
        )
    except Exception as exc:
        kind = {
            "backgroundUsageQuery": "query",
            "backgroundUsageDetail": "detail",
        }.get(action, "open")
        return runtime_settings.background_usage_response_status(
            kind,
            request_id,
            event_id=str(command.get("eventId") or "").strip(),
            error=f"用量总览读取失败：{exc}",
        )


def handle_cleanup_command(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object] | object:
    action = str(command.get("action") or "").strip()
    if action not in runtime_settings.SESSION_CLEANUP_COMMANDS:
        return UNHANDLED
    request_id = str(command.get("requestId") or command.get("id") or "")
    enqueue = getattr(ports.cleanup_worker, "enqueue", None)
    if not callable(enqueue):
        status = _status("会话永久删除当前不可用。", kind="error")
        status["sessionCleanupRequestId"] = request_id
        status["sessionCleanupAction"] = action
        return status
    try:
        accepted = enqueue(command)
    except Exception as exc:
        status = _status(str(exc), kind="error")
        status["sessionCleanupRequestId"] = request_id
        status["sessionCleanupAction"] = action
        return status
    request_id = str(accepted.get("requestId") or request_id)
    labels = {
        "sessionCleanupScan": "会话清单扫描已开始。",
        "sessionCleanupPreview": "正在生成永久删除确认。",
        "sessionCleanupExecute": "永久删除请求已进入本地事务门禁。",
        "sessionCleanupCancel": "已取消会话删除确认。",
    }
    status = _status(labels.get(action, "会话清理命令已提交。"))
    status["sessionCleanupRequestId"] = request_id
    status["sessionCleanupAction"] = action
    return status


def actionable_session_ids(payload: Mapping[str, object] | None) -> set[str]:
    if not isinstance(payload, Mapping):
        return set()
    result: set[str] = set()
    for window_name in ("today", "week", "month"):
        window = payload.get(window_name)
        if not isinstance(window, Mapping):
            continue
        for collection_name in ("sessions", "topSessionsByUsage", "topSessionsByCost"):
            sessions = window.get(collection_name)
            if not isinstance(sessions, list):
                continue
            for item in sessions:
                if not isinstance(item, Mapping) or not bool(
                    item.get("actionable", item.get("canActivate", False))
                ):
                    continue
                session_id = str(item.get("id") or item.get("sessionId") or "").strip()
                try:
                    canonical = str(uuid.UUID(session_id))
                except (ValueError, AttributeError, TypeError):
                    continue
                if canonical == session_id.casefold():
                    result.add(canonical)
    return result


def handle_insights_command(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object] | object:
    action = str(command.get("action") or "").strip()
    if action not in {"usageInsightsRefresh", "openUsageInsightsSession"}:
        return UNHANDLED
    request_id = str(command.get("requestId") or command.get("id") or "")
    if action == "usageInsightsRefresh":
        refresh = getattr(ports.insights_worker, "request_refresh", None)
        if not callable(refresh) or not refresh(request_id=request_id):
            status = _status("用量洞察刷新器当前不可用。", kind="error")
        else:
            status = _status("用量洞察刷新已开始。")
        status["usageInsightsRequestId"] = request_id
        return status
    session_id = str(command.get("sessionId") or "").strip().casefold()
    if session_id not in actionable_session_ids(ports.insights_payload):
        return _status(
            "该会话已归档、标识不完整或不在当前洞察结果中，未执行跳转。",
            kind="error",
        )
    if ports.activate_session is None:
        return _status("当前 Renderer 会话切换器不可用。", kind="error")
    result = ports.activate_session(
        {
            "action": "activateSession",
            "sessionId": session_id,
            "targetTitle": str(command.get("targetTitle") or "").strip(),
            "workdir": str(command.get("workdir") or "").strip(),
        }
    )
    if result is None or not (
        bool(getattr(result, "ok", False))
        or str(getattr(result, "status", "")) == "already-active"
    ):
        return _status(
            str(getattr(result, "message", "") or "无法打开该会话。"), kind="error"
        )
    return _status("已切换到所选会话。")


def _update_status(state: object, fallback: str) -> dict[str, object]:
    return _status(
        str(getattr(state, "message", "") or getattr(state, "title", "") or fallback),
        kind="error" if getattr(state, "error", "") else "",
    )


def handle_general_command(
    command: Mapping[str, Any], ports: GeneralCommandPorts
) -> dict[str, object]:
    from dataclasses import replace

    action = str(command.get("action") or "").strip()
    try:
        if action in {"save", "applyDisplayMode"}:
            settings_payload = command.get("settings")
            config = runtime_settings.config_from_payload(
                ports.load_config(), settings_payload
            )
            ports.save_config(config)
            if action == "applyDisplayMode":
                return _status(
                    "Renderer 方案已保存；当前会话已处于内嵌显示，无需重启。"
                )
            if str(command.get("section") or "") == "restReminder":
                started_at_ms = (
                    settings_payload.get("rest_reminder_timer_started_at_ms")
                    if isinstance(settings_payload, Mapping)
                    else None
                )
                if ports.rest_reminder is not None and started_at_ms is not None:
                    ports.rest_reminder.adjust_cycle_started_at_ms(started_at_ms)
                    status = _status("提醒设置已保存，已按指定时间校正本轮计时。")
                else:
                    status = _status("提醒设置已保存；休息结束后会自动开始下一轮。")
                status["restReminderSaved"] = True
                status["restReminderSaveRequestId"] = str(
                    command.get("requestId") or command.get("id") or ""
                )
                return status
            return _status("设置已保存，相关显示会自动刷新。")
        if action.startswith("restReminder"):
            reminder = ports.rest_reminder
            if action == "restReminderAck":
                if reminder is not None:
                    reminder.acknowledge()
                return _status("休息提醒状态已更新。")
            if action == "restReminderStart":
                ok = bool(reminder.start_rest()) if reminder is not None else False
                return _status(
                    "已开始休息计时。" if ok else "当前状态不能开始休息。",
                    kind="" if ok else "error",
                )
            if action == "restReminderFinish":
                ok = bool(reminder.finish_rest()) if reminder is not None else False
                return _status(
                    "本次休息已结束，新一轮专注计时已开始。"
                    if ok
                    else "当前没有正在进行的休息。",
                    kind="" if ok else "error",
                )
            if action == "restReminderPostpone":
                ok = bool(reminder.postpone()) if reminder is not None else False
                return _status(
                    "已安排稍后提醒。" if ok else "这次提醒已经延后过了。",
                    kind="" if ok else "error",
                )
            result = (
                reminder.test_notification()
                if reminder is not None
                else {"status": "failed", "error": "提醒服务未启动"}
            )
            sent = str(result.get("status") or "") == "sent"
            if bool(result.get("preview")):
                return _status(
                    "已发送系统通知，并弹出实际休息提醒预览。关闭预览不会改变当前计时。"
                    if sent
                    else f"已弹出实际休息提醒预览；系统通知失败：{result.get('error') or '未知错误'}",
                    kind="" if sent else "error",
                )
            return _status(
                "系统通知测试已发送。"
                if sent
                else f"系统通知发送失败：{result.get('error') or '未知错误'}",
                kind="" if sent else "error",
            )
        if action == "fetchPrices":
            config = runtime_settings.config_from_payload(
                ports.load_config(), command.get("settings")
            )
            provider = str(command.get("provider") or "").strip().lower()
            provider_url = (
                config.provider_settings[provider].pricing_url
                if provider and provider in config.provider_settings
                else config.pricing_url
            )
            prices = ports.fetch_prices(provider_url)
            config = config.with_price_updates(
                prices, pricing_url=provider_url, provider=provider or None
            )
            ports.save_config(config)
            return _status(f"已拉取并保存 {len(prices)} 个模型价格。")
        if action == "restart":
            ports.request_restart()
            return _status("已请求重启 HUD；daemon 模式会自动恢复。")
        if action == "exit":
            ports.request_exit()
            return _status("已请求退出 HUD；后台守护进程也会一并停止。")
        if action == "checkUpdate":
            if ports.update_manager is not None:
                return _update_status(
                    ports.update_manager.request_check(auto_download=False),
                    "正在检查更新...",
                )
            info = ports.check_update()
            if getattr(info, "error", ""):
                return _status(f"检查更新失败：{info.error}", kind="error")
            if getattr(info, "available", False):
                return _status(
                    f"发现新版本 {info.latest_version}，安装包：{info.asset_name}"
                )
            return _status(f"当前已是最新版本（{info.current_version}）。")
        if action == "installUpdate":
            if ports.update_manager is not None:
                return _update_status(
                    ports.update_manager.request_install(), "正在准备安装更新..."
                )
            info = ports.check_update()
            if getattr(info, "error", ""):
                return _status(f"检查更新失败：{info.error}", kind="error")
            if not getattr(info, "available", False):
                return _status(f"当前已是最新版本（{info.current_version}）。")
            ports.install_update(info)
            ports.request_restart()
            return _status(f"已启动 {info.asset_name}，安装器会先关闭当前 HUD。")
        if action == "installDesktopOverlay":
            status = ports.overlay_status()
            version = str(status.get("version") or "").strip()
            if bool(status.get("installed")):
                return _status(
                    f"气泡组件已可用{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。"
                )
            if bool(status.get("installing")):
                return _status("气泡组件正在安装；完成后点击“启用气泡”。")
            if not bool(status.get("canInstall")):
                return runtime_settings.settings_status(
                    "当前运行环境不能在线安装气泡组件；请安装带会话进度气泡的版本后重启 HUD。",
                    kind="error",
                    restart_visible=bool(status.get("requiresRestart")),
                )
            if ports.forced_missing_with_real_install():
                ports.clear_forced_missing()
                version = ports.pyside_version()
                return _status(
                    f"已检测到本机已安装气泡组件{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。"
                )
            if ports.start_overlay_install():
                refreshed = ports.overlay_status()
                if bool(refreshed.get("installed")):
                    version = str(refreshed.get("version") or "").strip()
                    return _status(
                        f"已检测到本机已安装气泡组件{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。"
                    )
                return _status("已开始安装气泡组件；完成后点击“启用气泡”。")
            return _status(
                "无法启动 PySide6 安装；请在终端运行 pip install PySide6>=6.8。",
                kind="error",
            )
        if action == "enableDesktopOverlay":
            if ports.forced_missing_with_real_install():
                ports.clear_forced_missing()
            status = ports.overlay_status()
            if not bool(status.get("installed")):
                return runtime_settings.settings_status(
                    "还没检测到气泡组件；安装完成后再点一次“启用气泡”。",
                    kind="error",
                    restart_visible=bool(status.get("requiresRestart")),
                )
            config = ports.load_config()
            if int(config.work_overlay_max_items or 0) <= 0:
                config = replace(
                    config, work_overlay_max_items=ports.default_overlay_limit()
                )
                ports.save_config(config)
            if ports.work_overlay is not None:
                ports.work_overlay.reset_runtime_availability()
            version = str(status.get("version") or "").strip()
            return _status(
                f"会话进度气泡已启用{f'（PySide6 {version}）' if version else ''}。"
            )
        if action == "updateAction":
            if ports.update_manager is None:
                return _status("当前会话未启用自动更新控制器。", kind="error")
            return _update_status(
                ports.update_manager.handle_click(), "更新操作已提交。"
            )
        if action == "dismissWarningsToday":
            if not ports.dismiss_warnings_today():
                return _status("无法保存预警关闭状态：配置路径不可用。", kind="error")
            return _status("今天不再显示预算预警。")
        return _status(f"无法处理未知设置命令：{action or 'empty'}", kind="error")
    except Exception as exc:
        return _status(f"设置命令执行失败：{exc}", kind="error")



def _handle_renderer_session_cleanup_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
) -> dict[str, object]:
    result = handle_cleanup_command(
        command,
        RuntimeCommandPorts(
            cleanup_worker=getattr(context, "session_cleanup_worker", None)
        ),
    )
    if result is UNHANDLED:
        return _renderer_settings_status("无法处理未知会话清理命令。", kind="error")
    return result


def _usage_insights_actionable_session_ids(context: object) -> set[str]:
    return actionable_session_ids(
        getattr(context, "usage_insights_payload", {})
    )


def _handle_renderer_usage_insights_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
    *,
    session_controller: SessionSwitchController | None,
) -> dict[str, object]:
    ports = RuntimeCommandPorts(
        insights_worker=getattr(context, "usage_insights_worker", None),
        insights_payload=getattr(context, "usage_insights_payload", None),
        activate_session=(
            lambda activation: _handle_work_overlay_command(
                activation,
                session_controller,
                prepare_window=True,
                backend_names=("cdp",),
            )
            if session_controller is not None
            else None
        ),
    )
    result = handle_insights_command(command, ports)
    if result is UNHANDLED:
        return _renderer_settings_status("无法处理未知用量洞察命令。", kind="error")
    return result


def _handle_renderer_settings_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
    restart_requested: Event,
    exit_requested: Event,
    update_manager: AutoUpdateManager | None = None,
    work_overlay: DesktopWorkOverlay | None = None,
    session_controller: SessionSwitchController | None = None,
) -> dict[str, object]:
    command_ports = RuntimeCommandPorts(
        background_usage=getattr(context, "background_usage_runtime", None),
        cleanup_worker=getattr(context, "session_cleanup_worker", None),
        insights_worker=getattr(context, "usage_insights_worker", None),
        insights_payload=getattr(context, "usage_insights_payload", None),
        activate_session=(
            lambda activation: _handle_work_overlay_command(
                activation,
                session_controller,
                prepare_window=True,
                backend_names=("cdp",),
            )
            if session_controller is not None
            else None
        ),
    )
    settings_store = getattr(context, "settings_store", None)
    settings_path = getattr(settings_store, "path", None)

    def load_config() -> UserConfig:
        load = getattr(settings_store, "load", None)
        return load() if callable(load) else UserConfig.defaults()

    def save_config(config: UserConfig) -> None:
        if settings_store is None:
            raise RuntimeError("配置存储当前不可用。")
        settings_store.save(config)
        context.settings_mtime = None
        context.reload_user_config()

    def install_update(info: object) -> None:
        installer = download_update_asset(info)
        launch_installer(installer)

    general_ports = GeneralCommandPorts(
        load_config=load_config,
        save_config=save_config,
        fetch_prices=fetch_model_prices,
        rest_reminder=getattr(context, "rest_reminder", None),
        update_manager=update_manager,
        work_overlay=work_overlay,
        request_restart=restart_requested.set,
        request_exit=exit_requested.set,
        check_update=lambda: check_for_update(current_version=__version__),
        install_update=install_update,
        overlay_status=_desktop_overlay_dependency_status,
        start_overlay_install=_start_desktop_overlay_install,
        clear_forced_missing=lambda: _set_force_desktop_overlay_missing(False),
        forced_missing_with_real_install=lambda: bool(
            _desktop_overlay_dependency_status().get("forcedMissing")
            and _desktop_overlay_dependency_status().get("realInstalled")
        ),
        pyside_version=_pyside6_version,
        default_overlay_limit=lambda: min(
            DEFAULT_WORK_OVERLAY_MAX_ITEMS, _work_overlay_screen_max_items()
        ),
        dismiss_warnings_today=lambda: bool(
            settings_path is not None and not dismiss_warning_for_today(settings_path)
        ),
    )
    return dispatch_command(command, command_ports, general_ports)

__all__ = [
    "RuntimeCommandPorts",
    "GeneralCommandPorts",
    "UNHANDLED",
    "actionable_session_ids",
    "correlate_status",
    "dispatch_command",
    "handle_background_command",
    "handle_cleanup_command",
    "handle_insights_command",
    "handle_general_command",
]
