"""Qt-free work-overlay item normalization and projection rules."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, MutableMapping, Sequence
from datetime import datetime
from typing import Any

from ... import overlay_projection
from ...core import parse_timestamp
from .constants import WORK_OVERLAY_HOTSPOT_HOVER_ALPHA

def _compact_work_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."

def _compact_workdir_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return "..." + text[-max(0, limit - 3) :]

def _normalized_system_action(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    action_id = str(value.get("id") or "").strip()
    action = str(value.get("action") or "").strip()
    title = str(value.get("title") or "").strip()
    label = str(value.get("label") or "").strip()
    if not action_id or not action or not title or not label:
        return None
    return {
        "id": action_id,
        "action": action,
        "title": title,
        "message": str(value.get("message") or "").strip(),
        "label": label,
        "persistent": bool(value.get("persistent")),
    }

def _system_action_overlay_item(action: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": str(action.get("id") or ""),
        "title": str(action.get("title") or ""),
        "status": "warning",
        "statusLabel": str(action.get("label") or ""),
        "statusText": str(action.get("label") or ""),
        "lastText": str(action.get("message") or ""),
        "elapsedText": "",
        "systemAction": True,
        "action": str(action.get("action") or ""),
    }

def _item_is_system_action(item: Mapping[str, object]) -> bool:
    return bool(item.get("systemAction")) and bool(str(item.get("action") or "").strip())

def _normalized_system_notice(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    notice_id = str(value.get("id") or "").strip()
    title = str(value.get("title") or "").strip()
    message = str(value.get("message") or "").strip()
    if not notice_id or not title or not message:
        return None
    status = str(value.get("status") or "warning").strip() or "warning"
    return {
        "id": notice_id,
        "title": title,
        "message": message,
        "status": status,
        "persistent": bool(value.get("persistent")),
    }

def _system_notice_overlay_item(notice: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": str(notice.get("id") or ""),
        "title": str(notice.get("title") or ""),
        "status": str(notice.get("status") or "warning"),
        "statusLabel": "请稍候",
        "statusText": "请稍候",
        "lastText": str(notice.get("message") or ""),
        "elapsedText": "",
        "systemNotice": True,
    }

def _item_is_system_notice(item: Mapping[str, object]) -> bool:
    return bool(item.get("systemNotice"))

def _normalized_rest_reminder(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or not bool(value.get("bubbleVisible")):
        return None
    phase = str(value.get("phase") or "").strip().lower()
    if phase not in {"prompt", "postponed", "resting", "completed", "preview"}:
        return None
    return {
        "bubbleVisible": True,
        "phase": phase,
        "message": str(value.get("message") or "").strip(),
        "canPostpone": bool(value.get("canPostpone")),
        "intervalMinutes": max(1, int(value.get("intervalMinutes") or 45)),
        "breakMinutes": max(1, int(value.get("breakMinutes") or 2)),
        "postponeMinutes": max(1, int(value.get("postponeMinutes") or 10)),
        "promptEndsAtMs": max(0, int(value.get("promptEndsAtMs") or 0)),
        "promptStartedAtMs": max(0, int(value.get("promptStartedAtMs") or 0)),
        "promptWaitInfinite": bool(value.get("promptWaitInfinite")),
        "earlyRestOptionsMinutes": [3, 5, 10],
        "postponeEndsAtMs": max(0, int(value.get("postponeEndsAtMs") or 0)),
        "restStartedAtMs": max(0, int(value.get("restStartedAtMs") or 0)),
        "restEndsAtMs": max(0, int(value.get("restEndsAtMs") or 0)),
        "todayRestedSeconds": max(0, int(value.get("todayRestedSeconds") or 0)),
        "completedTodaySeconds": max(
            0, int(value.get("completedTodaySeconds") or 0)
        ),
        "lastRestDurationSeconds": max(
            0, int(value.get("lastRestDurationSeconds") or 0)
        ),
        "completionEndsAtMs": max(0, int(value.get("completionEndsAtMs") or 0)),
    }

def _item_is_rest_reminder(item: Mapping[str, object]) -> bool:
    return str(item.get("kind") or "").strip() == "rest_reminder"

def _format_rest_duration(value: object) -> str:
    try:
        total = max(0, int(round(float(value))))
    except (TypeError, ValueError, OverflowError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"

def _rest_credit_actions() -> list[dict[str, object]]:
    return [
        *(
            {
                "action": "restReminderCredit",
                "label": f"{minutes}分钟",
                "minutes": minutes,
                "primary": False,
            }
            for minutes in (3, 5, 10)
        ),
        {"action": "restReminderCreditMore", "label": "更多", "primary": False},
    ]


def _rest_reminder_card_copy(
    item: Mapping[str, object],
    *,
    now_ms: int | None = None,
) -> dict[str, object]:
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    phase = str(item.get("phase") or "").strip().lower()
    completed_today = max(0, int(item.get("completedTodaySeconds") or 0))
    message = str(item.get("message") or "").strip() or "该休息一下了。"
    title = "☕ 休息提醒"
    detail = message
    hint = ""
    status = ""
    header_meta = ""
    header_elapsed = ""
    color_status = "waiting_user"
    actions: list[dict[str, object]] = []
    if phase == "prompt":
        title = "☕ 该休息一下了"
        hint = "如果您已提前休息过了，可以点击下方分钟数按钮标记已休息"
        header_meta = f"今日已休息 {_format_rest_duration(completed_today)}"
        prompt_started_ms = int(item.get("promptStartedAtMs") or 0)
        prompt_elapsed = (
            max(0.0, (current_ms - prompt_started_ms) / 1000.0)
            if prompt_started_ms > 0
            else 0.0
        )
        header_elapsed = f"已等待 {_format_rest_duration(prompt_elapsed)}"
        status = "等待你的选择 · 不会自动跳过"
        if bool(item.get("canPostpone")):
            minutes = max(1, int(item.get("postponeMinutes") or 10))
            actions.append(
                {
                    "action": "restReminderPostpone",
                    "label": f"延迟 {minutes} 分钟",
                    "primary": False,
                }
            )
        actions.extend(_rest_credit_actions())
        actions.append({"action": "restReminderStart", "label": "开始休息", "primary": True})
    elif phase == "postponed":
        remaining = max(
            0.0, (int(item.get("postponeEndsAtMs") or 0) - current_ms) / 1000.0
        )
        title = "☕ 休息已延迟"
        header_meta = f"今日已休息 {_format_rest_duration(completed_today)}"
        detail = f"{_format_rest_duration(remaining)} 后再次提醒"
        hint = "如果您已经休息过了，可以点击分钟数按钮标记已休息"
        status = "延迟不计入休息"
        color_status = "tool"
        actions.extend(_rest_credit_actions())
        actions.append(
            {"action": "restReminderStart", "label": "开始休息", "primary": True}
        )
    elif phase == "resting":
        started_ms = int(item.get("restStartedAtMs") or 0)
        ends_ms = int(item.get("restEndsAtMs") or 0)
        elapsed = max(0.0, (min(current_ms, ends_ms or current_ms) - started_ms) / 1000.0)
        today = max(
            completed_today + elapsed,
            int(item.get("todayRestedSeconds") or 0),
        )
        target = max(1, int(item.get("breakMinutes") or 2)) * 60
        title = "☕ 正在休息"
        header_meta = f"今日已休息 {_format_rest_duration(today)}"
        detail = f"本次已休息 {_format_rest_duration(elapsed)}"
        hint = "实际休息比计时更长？点击分钟数按钮按实际时长记录并结束"
        status = f"目标 {_format_rest_duration(target)}"
        color_status = "running"
        actions.extend(_rest_credit_actions())
        actions.append(
            {"action": "restReminderFinish", "label": "提前结束", "primary": True}
        )
    elif phase == "completed":
        duration = max(0, int(item.get("lastRestDurationSeconds") or 0))
        title = "✓ 休息完成"
        header_meta = f"今日已休息 {_format_rest_duration(completed_today)}"
        detail = f"本次休息 {_format_rest_duration(duration)}"
        status = "新一轮专注已开始"
        color_status = "rest_completed"
    elif phase == "preview":
        title = "测试预览"
        detail = message
        status = "不会改变当前计时，也不会计入今日休息"
        color_status = "tool"
        actions.append(
            {"action": "restReminderAck", "label": "关闭预览", "primary": True}
        )
    return {
        "title": title,
        "headerMeta": header_meta,
        "headerElapsed": header_elapsed,
        "detail": detail,
        "hint": hint,
        "statusText": status,
        "status": color_status,
        "actions": actions,
    }

def _rest_reminder_overlay_item(reminder: Mapping[str, object]) -> dict[str, object]:
    normalized = _normalized_rest_reminder(reminder)
    if normalized is None:
        return {}
    copy = _rest_reminder_card_copy(normalized)
    return {
        "id": "rest-reminder",
        "kind": "rest_reminder",
        "phase": normalized["phase"],
        "message": normalized["message"],
        "canPostpone": normalized["canPostpone"],
        "intervalMinutes": normalized["intervalMinutes"],
        "breakMinutes": normalized["breakMinutes"],
        "postponeMinutes": normalized["postponeMinutes"],
        "promptEndsAtMs": normalized["promptEndsAtMs"],
        "promptStartedAtMs": normalized["promptStartedAtMs"],
        "promptWaitInfinite": normalized["promptWaitInfinite"],
        "earlyRestOptionsMinutes": normalized["earlyRestOptionsMinutes"],
        "postponeEndsAtMs": normalized["postponeEndsAtMs"],
        "restStartedAtMs": normalized["restStartedAtMs"],
        "restEndsAtMs": normalized["restEndsAtMs"],
        "todayRestedSeconds": normalized["todayRestedSeconds"],
        "completedTodaySeconds": normalized["completedTodaySeconds"],
        "lastRestDurationSeconds": normalized["lastRestDurationSeconds"],
        "completionEndsAtMs": normalized["completionEndsAtMs"],
        "title": copy["title"],
        "headerMeta": copy["headerMeta"],
        "headerElapsed": copy["headerElapsed"],
        "status": copy["status"],
        "statusLabel": copy["statusText"],
        "statusText": copy["statusText"],
        "lastText": copy["detail"],
        "restHint": copy["hint"],
        "elapsedText": "",
        "restActions": copy["actions"],
    }

def _workdir_parts(value: object) -> list[str]:
    text = str(value or "").strip().rstrip("\\/")
    if not text:
        return []
    return [part for part in text.replace("/", "\\").split("\\") if part]

def _workdir_leaf(value: object) -> str:
    parts = _workdir_parts(value)
    return parts[-1] if parts else ""

def _item_is_background_usage(item: Mapping[str, object]) -> bool:
    return (
        str(item.get("kind") or "").strip() == "background_usage"
        or str(item.get("status") or "").strip() == "background_usage"
    )

def _workdir_display_name(item: Mapping[str, object]) -> str:
    if _item_is_background_usage(item):
        return str(item.get("workdirName") or "查看后台用量记录").strip()
    return _workdir_leaf(item.get("workdir")) or _workdir_leaf(item.get("workdirName"))

def _item_is_completed(item: Mapping[str, object]) -> bool:
    if _item_is_background_usage(item):
        return False
    return str(item.get("status") or "") == "recent"


OverlayRect = tuple[float, float, float, float]

def _item_id(item: Mapping[str, object]) -> str:
    return str(item.get("id") or "").strip()

def _switch_item_key(item: Mapping[str, object]) -> str:
    session_id = str(item.get("sessionId") or item.get("id") or "").strip()
    title = str(item.get("targetTitle") or item.get("title") or "").strip()
    workdir = str(item.get("workdir") or "").strip()
    if not session_id and not title and not workdir:
        return ""
    return json.dumps(
        {
            "sessionId": session_id,
            "title": title,
            "workdir": workdir,
        },
        ensure_ascii=False,
        sort_keys=True,
    )

def _item_kind(item: Mapping[str, object]) -> str:
    return "completed" if _item_is_completed(item) else "card"

def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))

def _interactive_hotspot_opacity(
    base_opacity: float,
    hovered: bool,
    *,
    invisible_hit_surface: bool = False,
) -> float:
    if hovered:
        return WORK_OVERLAY_HOTSPOT_HOVER_ALPHA
    if invisible_hit_surface:
        # Windows performs per-pixel hit testing for these layered top-level
        # windows. Keep their global opacity at 1 so the intentionally faint
        # painted pixels never round down to fully transparent while the parent
        # bubble fades under the pointer.
        return 1.0
    return _clamp01(base_opacity)

def _matched_overlay_item_records(
    records: Sequence[dict[str, Any]],
    items: Sequence[Mapping[str, object]],
) -> list[tuple[dict[str, Any], Mapping[str, object]]]:
    """Match grouped widget records to payload items by shape and stable ID."""
    unused_records = list(records)
    matched: list[tuple[dict[str, Any], Mapping[str, object]]] = []
    for item in items:
        item_id = _item_id(item)
        kind = _item_kind(item)
        record = next(
            (
                candidate
                for candidate in unused_records
                if str(candidate.get("kind") or "") == kind
                and (
                    not item_id
                    or str(candidate.get("item_id") or "") == item_id
                )
            ),
            None,
        )
        if record is None:
            continue
        unused_records.remove(record)
        matched.append((record, item))
    return matched

def _workdir_link_pending_for_item(
    item: Mapping[str, object],
    pending: bool,
) -> bool:
    del item, pending
    return False

def _item_is_cli(item: Mapping[str, object]) -> bool:
    return str(item.get("clientKind") or "").strip().lower() == "cli"

def _profile_display_name(item: Mapping[str, object]) -> str:
    """Return the profile label only for CLI session bubbles."""
    if not _item_is_cli(item):
        return ""
    profile = _compact_work_text(
        item.get("profileName")
        or item.get("profile_name")
        or item.get("modelProvider"),
        24,
    )
    if profile.lower() in {"unknown", "n/a"}:
        return ""
    return profile

def _workdir_footer_display_name(
    item: Mapping[str, object],
    *,
    limit: int = 40,
) -> str:
    """Return the existing footer workdir text with a CLI profile prefix."""
    workdir = _workdir_display_name(item)
    profile = _profile_display_name(item)
    if not profile:
        return _compact_workdir_text(workdir, limit)
    profile_label = f"[{profile}]"
    if not workdir:
        return profile_label
    remaining = max(8, limit - len(profile_label) - 1)
    return f"{profile_label} {_compact_workdir_text(workdir, remaining)}"

def _workdir_clickable_for_item(item: Mapping[str, object]) -> bool:
    if _item_is_cli(item):
        return False
    if _item_is_background_usage(item):
        return bool(str(item.get("eventId") or item.get("id") or "").strip())
    workdir = str(item.get("workdir") or "").strip()
    session_id = str(item.get("sessionId") or item.get("id") or "").strip()
    target_title = str(item.get("targetTitle") or item.get("title") or "").strip()
    return bool(workdir and (session_id or target_title))

def _workdir_link_hover_visible_for_item(item: Mapping[str, object]) -> bool:
    return not _item_is_cli(item) and not _item_is_completed(item)

def _workdir_external_link_for_item(item: Mapping[str, object]) -> bool:
    return _workdir_clickable_for_item(item)

def _workdir_link_opacity_for_item(
    item: Mapping[str, object],
    base_opacity: float,
    hovered: bool,
) -> float:
    return _interactive_hotspot_opacity(
        base_opacity,
        hovered and _workdir_link_hover_visible_for_item(item),
        invisible_hit_surface=True,
    )

def _overlay_item_timestamp_seconds(
    item: Mapping[str, object],
    *keys: str,
) -> float:
    return overlay_projection.payload_timestamp_seconds(
        item,
        *keys,
        parse_timestamp=parse_timestamp,
    )

def _ordered_overlay_items(items: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return overlay_projection.order_payload_items(
        items,
        is_background=_item_is_background_usage,
        is_completed=_item_is_completed,
        parse_timestamp=parse_timestamp,
    )

def _work_overlay_header_text(
    started_at: datetime | str | None,
    elapsed_text: str,
    title: str,
    *,
    title_limit: int = 28,
) -> str:
    timestamp = started_at
    if isinstance(started_at, str):
        timestamp = parse_timestamp(started_at)
    start_text = ""
    if isinstance(timestamp, datetime):
        start_text = (
            timestamp.astimezone().strftime("%H:%M:%S")
            if timestamp.tzinfo is not None
            else timestamp.strftime("%H:%M:%S")
        )
    title_text = _compact_work_text(title, title_limit)
    parts = [part for part in (start_text, elapsed_text.strip(), title_text) if part]
    return " | ".join(parts) if parts else "Codex 工作"

def _work_overlay_live_elapsed_text(
    item: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> str | None:
    """Return local elapsed copy for an active session card, never terminal items."""
    status = str(item.get("status") or "").strip().lower()
    if (
        status not in {"running", "active", "tool"}
        or _item_is_completed(item)
        or _item_is_background_usage(item)
        or _item_is_rest_reminder(item)
        or _item_is_system_action(item)
        or _item_is_system_notice(item)
    ):
        return None
    started_at = parse_timestamp(
        str(item.get("taskStartedAt") or item.get("startedAt") or "").strip()
    )
    if started_at is None:
        return None
    current = now or datetime.now().astimezone()
    if started_at.tzinfo is None:
        current = current.replace(tzinfo=None)
    else:
        current = current.astimezone(started_at.tzinfo)
    seconds = max(0, int((current - started_at).total_seconds()))
    if seconds < 60:
        elapsed = f"{seconds}s"
    else:
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            elapsed = f"{minutes}m{seconds:02d}s"
        else:
            hours, minutes = divmod(minutes, 60)
            elapsed = f"{hours}h{minutes:02d}m{seconds:02d}s"
    return f"已处理 {elapsed}"

def _work_overlay_item_with_live_elapsed_text(
    item: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Normalize incoming active-card elapsed copy before each render."""
    normalized = dict(item)
    elapsed_text = _work_overlay_live_elapsed_text(normalized, now=now)
    if elapsed_text is not None:
        normalized["elapsedText"] = elapsed_text
    return normalized

def _item_dismiss_key(item: Mapping[str, object]) -> str:
    status = str(item.get("status") or "")
    error_text = str(item.get("statusText") or item.get("detail") or "") if status == "error" else ""
    return json.dumps(
        {
            "id": item.get("id"),
            "errorText": error_text,
            "status": "error" if status == "error" else "work",
            "taskStartedAt": item.get("taskStartedAt") or item.get("startedAt") or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )

def _mark_item_dismissed(
    dismissed_instances: MutableMapping[str, str],
    item: Mapping[str, object],
) -> None:
    item_id = _item_id(item)
    if item_id:
        dismissed_instances[item_id] = _item_dismiss_key(item)

def _visible_overlay_items(
    items: Sequence[Mapping[str, object]],
    dismissed_instances: MutableMapping[str, str],
    *,
    item_limit: int,
) -> list[Mapping[str, object]]:
    return overlay_projection.visible_payload_items(
        items,
        dismissed_instances,
        item_limit=item_limit,
        dismiss_key=_item_dismiss_key,
    )

__all__ = [
    "_compact_work_text",
    "_compact_workdir_text",
    "_normalized_system_action",
    "_system_action_overlay_item",
    "_item_is_system_action",
    "_normalized_system_notice",
    "_system_notice_overlay_item",
    "_item_is_system_notice",
    "_normalized_rest_reminder",
    "_item_is_rest_reminder",
    "_format_rest_duration",
    "_rest_reminder_card_copy",
    "_rest_reminder_overlay_item",
    "_workdir_parts",
    "_workdir_leaf",
    "_item_is_background_usage",
    "_workdir_display_name",
    "_item_is_completed",
    "_item_id",
    "_switch_item_key",
    "_item_kind",
    "_clamp01",
    "_interactive_hotspot_opacity",
    "_workdir_link_pending_for_item",
    "_item_is_cli",
    "_profile_display_name",
    "_workdir_footer_display_name",
    "_workdir_clickable_for_item",
    "_workdir_link_hover_visible_for_item",
    "_workdir_external_link_for_item",
    "_workdir_link_opacity_for_item",
    "_matched_overlay_item_records",
    "_overlay_item_timestamp_seconds",
    "_ordered_overlay_items",
    "_work_overlay_header_text",
    "_work_overlay_live_elapsed_text",
    "_work_overlay_item_with_live_elapsed_text",
    "_item_dismiss_key",
    "_mark_item_dismissed",
    "_visible_overlay_items",
]
