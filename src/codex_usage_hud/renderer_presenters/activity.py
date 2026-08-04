"""Pure activity-event and tool-detail presentation helpers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from ..core.parser import RequestRound, ToolCallTiming
from .common import format_fixed_money, short_num, timeline_time


CompactFn = Callable[[Any, int], str]
TimelineTimeFn = Callable[[datetime | None], str]
TokenConfirmFn = Callable[[str, str], bool]
RoundCostFn = Callable[[RequestRound, str], tuple[float | None, bool]]


def tool_call_arguments_summary(
    call: ToolCallTiming,
    *,
    compact: CompactFn,
) -> str:
    raw_args = (call.args or "").strip()
    if not raw_args:
        return ""
    try:
        payload = json.loads(raw_args)
    except json.JSONDecodeError:
        return compact(raw_args, 96)
    if isinstance(payload, dict):
        for key in ("command", "query", "q", "url", "path"):
            value = payload.get(key)
            if value:
                return compact(value, 96)
    return compact(raw_args, 96)


def tool_call_timeline_detail(
    call: ToolCallTiming,
    duration: str,
    *,
    arguments_summary: Callable[[ToolCallTiming], str],
) -> str:
    args = arguments_summary(call)
    if args:
        return f"{duration} {call.name} · {args}"
    return f"{duration} {call.name}"


def is_token_confirm_event(title: str, detail: str) -> bool:
    return title == "Token确认" or "received token_count" in detail


def merge_activity_events(
    events: list[tuple[datetime, int, dict[str, object]]],
    *,
    compact: CompactFn,
    timeline_time_fn: TimelineTimeFn = timeline_time,
    token_confirm_event: TokenConfirmFn = is_token_confirm_event,
) -> list[dict[str, object]]:
    grouped: dict[str, list[tuple[datetime, int, dict[str, object]]]] = {}
    for event in events:
        moment = event[0]
        key = moment.astimezone().replace(microsecond=0).isoformat()
        grouped.setdefault(key, []).append(event)

    merged: list[tuple[datetime, int, dict[str, object], bool]] = []
    for group in grouped.values():
        group.sort(key=lambda item: item[1])
        moment = max(item[0] for item in group)
        order = max(item[1] for item in group)
        group_titles = [str(item[2].get("title") or "") for item in group]
        suppress_request_complete = "请求完成" in group_titles and any(
            title in group_titles for title in ("任务完成", "任务中止")
        )
        suppress_round_title = any(title in group_titles for title in ("任务完成", "任务中止"))
        meaningful_titles: list[str] = []
        token_titles: list[str] = []
        details: list[str] = []
        tooltip_lines: list[str] = []
        active = False
        has_meaningful = False
        for _moment, _order, item in group:
            title = str(item.get("title") or "")
            detail = str(item.get("detail") or "")
            token_confirm = token_confirm_event(title, detail)
            if suppress_request_complete and title == "请求完成":
                active = active or bool(item.get("active"))
                continue
            if suppress_round_title and title.startswith("轮次 #"):
                active = active or bool(item.get("active"))
                continue
            title_bucket = token_titles if token_confirm else meaningful_titles
            if title and title not in title_bucket:
                title_bucket.append(title)
            has_meaningful = has_meaningful or not token_confirm
            if detail and not token_confirm and detail not in details:
                details.append(detail)
            tooltip = str(item.get("tooltip") or "").strip()
            if tooltip and not token_confirm and tooltip not in tooltip_lines:
                tooltip_lines.append(tooltip)
            active = active or bool(item.get("active"))

        titles = meaningful_titles + token_titles
        title_text = "，".join(titles) if titles else "活动"
        detail_text = "；".join(details)
        tooltip = "\n".join(tooltip_lines) if tooltip_lines else title_text
        merged.append(
            (
                moment,
                order,
                {
                    "time": timeline_time_fn(moment),
                    "title": compact(title_text, 40),
                    "detail": compact(detail_text, 96),
                    "tooltip": compact(
                        f"{timeline_time_fn(moment)}  {title_text}\n{tooltip}",
                        320,
                    ),
                    "active": active,
                },
                has_meaningful,
            )
        )

    meaningful = [item for item in merged if item[3]]
    if meaningful:
        merged = meaningful
    merged.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in merged]


def activity_round_detail(
    item: RequestRound,
    fallback_model: str,
    *,
    round_cost: RoundCostFn,
    compact: CompactFn,
    fixed_money: Callable[[float | None, bool], str] = format_fixed_money,
    short_number: Callable[[int | None], str] = short_num,
) -> str:
    cost, estimated = round_cost(item, fallback_model)
    total = int(item.total_tokens or 0)
    if total <= 0:
        total = int(item.input_tokens or 0) + int(item.output_tokens or 0)
    parts = [
        fixed_money(cost, estimated),
        f"∑{short_number(total)}",
    ]
    summary = compact(item.activity_summary, 64)
    if summary:
        parts.append(summary)
    else:
        parts.append(
            " ".join(
                [
                    f"↑{short_number(item.input_tokens)}",
                    f"↻{short_number(item.cached_tokens)}",
                    f"↓{short_number(item.output_tokens)}",
                    f"◇{short_number(item.reasoning_tokens)}",
                ]
            )
        )
    return " · ".join(parts)


__all__ = [
    "activity_round_detail",
    "is_token_confirm_event",
    "merge_activity_events",
    "tool_call_arguments_summary",
    "tool_call_timeline_detail",
]
