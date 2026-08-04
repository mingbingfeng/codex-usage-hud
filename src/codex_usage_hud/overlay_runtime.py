"""Production glue between overlay domains and runtime context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging

from . import overlay_commands, overlay_projection
from .core import WorkStatusItem
from .core.background_usage import background_feature_label
from .desktop_overlay import DesktopWorkOverlay
from .overlay_commands import (
    _prepare_codex_window_for_work_overlay_switch,
    _refocus_codex_window_after_current_session_click,
    _refocus_codex_window_after_work_overlay_switch,
)
from .platforms import SessionSwitchController, SessionSwitchResult
from .runtime_usage import (
    format_cost_compact as _format_cost_compact,
    format_tokens as _format_tokens,
)


_LOGGER = logging.getLogger(__name__)

def _handle_work_overlay_command(
    command: Mapping[str, object],
    session_controller: SessionSwitchController,
    **kwargs: object,
) -> SessionSwitchResult | None:
    return overlay_commands.handle_command(
        command,
        session_controller,
        prepare_window_callback=_prepare_codex_window_for_work_overlay_switch,
        refocus_window_callback=_refocus_codex_window_after_current_session_click,
        **kwargs,
    )


def _handle_work_overlay_commands(
    work_overlay: DesktopWorkOverlay,
    session_controller: SessionSwitchController,
    **kwargs: object,
) -> int:
    return overlay_commands.handle_commands(
        work_overlay,
        session_controller,
        prepare_window_callback=_prepare_codex_window_for_work_overlay_switch,
        refocus_window_callback=_refocus_codex_window_after_current_session_click,
        background_refocus_callback=_refocus_codex_window_after_work_overlay_switch,
        **kwargs,
    )


def background_usage_to_work_item(
    summary: Mapping[str, object],
) -> WorkStatusItem | None:
    return overlay_projection.background_usage_to_work_item(
        summary,
        feature_label=background_feature_label,
        format_tokens=_format_tokens,
        format_cost=_format_cost_compact,
    )


def _background_usage_work_items(context: object) -> list[WorkStatusItem]:
    runtime = getattr(context, "background_usage_runtime", None)
    pending_today = getattr(runtime, "pending_today", None)
    if not callable(pending_today):
        return []
    try:
        summaries = pending_today()
    except Exception as exc:
        _LOGGER.debug("background_usage_overlay_query_failed error=%s", exc)
        return []
    items: list[WorkStatusItem] = []
    for summary in summaries:
        if not isinstance(summary, Mapping):
            continue
        item = background_usage_to_work_item(summary)
        if item is not None:
            items.append(item)
    return items


def _background_usage_notification_for_session(
    context: object,
    session_id: object,
) -> dict[str, object]:
    runtime = getattr(context, "background_usage_runtime", None)
    notification_for_session = getattr(runtime, "notification_for_session", None)
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id or not callable(notification_for_session):
        return {}
    try:
        raw = notification_for_session(normalized_session_id)
    except Exception as exc:
        _LOGGER.debug(
            "background_usage_notification_query_failed session_id=%s error=%s",
            normalized_session_id,
            exc,
        )
        return {}
    if not isinstance(raw, Mapping):
        return {}
    try:
        count = max(0, int(raw.get("count") or 0))
    except (TypeError, ValueError, OverflowError):
        return {}
    event_id = str(raw.get("eventId") or "").strip()
    if count <= 0 or not event_id:
        return {}
    range_key = str(raw.get("range") or "today").strip().lower()
    if range_key not in {"today", "7d", "30d", "all"}:
        range_key = "today"
    return {"count": count, "eventId": event_id, "range": range_key}


def _work_overlay_items_with_background_usage(
    context: object,
    session_items: Sequence[WorkStatusItem],
) -> list[WorkStatusItem]:
    return overlay_projection.append_background_usage(
        session_items,
        _background_usage_work_items(context),
    )
