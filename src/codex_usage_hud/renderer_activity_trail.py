"""Pure activity-trail projection for Renderer top details.

The trail owner turns one parsed session into ordered, compact event nodes.  It
does not own task state decisions or payload envelopes; formatting/domain
callbacks are supplied explicitly so the projection remains usable without
importing the payload builder.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Protocol

from .core.parser import ParsedSession, RequestRound
from .renderer_presenters import activity as renderer_activity


class ActivityTrailContext(Protocol):
    """Structural callback contract consumed by :func:`activity_trail`."""

    task_rows: Callable[[ParsedSession], list[RequestRound]]
    round_cost: Callable[[RequestRound, str], tuple[float | None, bool]]
    compact: Callable[[Any, int], str]
    activity_label: Callable[[str], str]
    request_status_label: Callable[[str], str]
    gap_label: Callable[[str], str]
    short_number: Callable[[int | None], str]
    format_fixed_money: Callable[[float | None, bool], str]
    duration_text: Callable[[float | None], str]
    timeline_time: Callable[[datetime | None], str]


def _activity_round_detail(
    item: RequestRound,
    fallback_model: str,
    *,
    context: ActivityTrailContext,
) -> str:
    return renderer_activity.activity_round_detail(
        item,
        fallback_model,
        round_cost=context.round_cost,
        compact=context.compact,
        fixed_money=context.format_fixed_money,
        short_number=context.short_number,
    )


def _running_duration(
    start: datetime | None,
    end: datetime | None,
    now: datetime,
) -> float | None:
    if start is None:
        return None
    finish = (
        end or now.astimezone(start.tzinfo)
        if start.tzinfo is not None
        else end or now.replace(tzinfo=None)
    )
    return max(0.0, (finish - start).total_seconds())


def activity_trail(
    snapshot: ParsedSession,
    *,
    context: ActivityTrailContext,
    current_task: Callable[[ParsedSession], str],
    activity_main: Callable[[ParsedSession, int], str],
) -> list[dict[str, object]]:
    """Project the session's visible activity events in newest-first order."""
    now = snapshot.refreshed_at or datetime.now().astimezone()
    events: list[tuple[datetime, int, dict[str, object]]] = []
    seen: set[tuple[str, str, str]] = set()
    order = 0
    row_times = [
        moment
        for item in context.task_rows(snapshot)
        for moment in (item.started_at, item.completed_at)
        if moment is not None
    ]
    task_start = snapshot.task_started_at or (min(row_times) if row_times else None)

    def add(
        moment: datetime | None,
        title: str,
        detail: str,
        *,
        active: bool = False,
        round_index: int = 0,
    ) -> None:
        nonlocal order
        if moment is None:
            return
        if task_start is not None:
            current = (
                moment.astimezone(task_start.tzinfo)
                if task_start.tzinfo
                else moment.replace(tzinfo=None)
            )
            start = (
                task_start
                if task_start.tzinfo
                else task_start.replace(tzinfo=None)
            )
            if current < start:
                return
        key = (moment.astimezone().isoformat(), title, detail)
        if key in seen:
            return
        seen.add(key)
        order += 1
        event: dict[str, object] = {
            "time": context.timeline_time(moment),
            "title": context.compact(title, 26),
            "detail": context.compact(detail, 72),
            "tooltip": context.compact(
                f"{context.timeline_time(moment)}  {title}  {detail}",
                260,
            ),
            "active": active,
        }
        task_index = int(getattr(snapshot, "task_index", 0) or 0)
        if task_index > 0:
            event["taskIndex"] = task_index
        if round_index > 0:
            event["roundIndex"] = round_index
            event["roundIndexes"] = [round_index]
        events.append(
            (
                moment,
                order,
                event,
            )
        )

    add(snapshot.task_started_at, "任务开始", current_task(snapshot))
    for item in context.task_rows(snapshot):
        moment = item.completed_at or item.started_at
        if moment is None:
            continue
        title = f"轮次 #{item.index}"
        active = item.status == "running" and item.completed_at is None
        add(
            moment,
            title,
            _activity_round_detail(
                item,
                snapshot.request.model,
                context=context,
            ),
            active=active,
            round_index=int(item.index or 0),
        )
    add(
        snapshot.request.started_at,
        "请求开始",
        snapshot.request.model
        or context.request_status_label(snapshot.request.status),
        active=snapshot.request.status == "running"
        and snapshot.request.completed_at is None,
    )
    for step in getattr(snapshot, "activity_steps", []) or []:
        status = str(getattr(step, "status", "") or "").strip().lower()
        detail = str(getattr(step, "detail", "") or "").strip()
        if status == "failed":
            detail = f"失败：{detail}" if detail else "命令执行失败"
        add(
            getattr(step, "timestamp", None),
            str(getattr(step, "title", "执行命令") or "执行命令"),
            detail,
            active=status == "running",
        )
    call = snapshot.slow.slowest_tool_call
    if call is not None:
        duration = context.duration_text(_running_duration(call.start, call.end, now))
        args = renderer_activity.tool_call_arguments_summary(
            call,
            compact=context.compact,
        )
        detail = renderer_activity.tool_call_timeline_detail(
            call,
            duration,
            arguments_summary=lambda _call: args,
        )
        add(call.start, "工具调用", detail, active=call.end is None)
        completion_detail = detail
        if call.output:
            completion_detail = f"{completion_detail} · 返回 {context.compact(call.output, 80)}"
        add(call.end, "工具完成", completion_detail)
    gap = snapshot.slow.longest_gap_detail
    if gap is not None:
        label = context.gap_label(gap.category)
        add(gap.start, "等待开始", f"{label}：{gap.from_event}")
        add(
            gap.end,
            "等待结束",
            f"{context.duration_text(gap.duration_seconds)} {label}：{gap.to_event}",
        )
    add(
        snapshot.activity.timestamp,
        context.activity_label(snapshot.activity.kind),
        snapshot.activity.detail,
        active=True,
    )
    add(snapshot.request.completed_at, "请求完成", snapshot.request.model or "模型请求")
    add(snapshot.task_completed_at, "任务完成", current_task(snapshot))
    add(snapshot.task_aborted_at, "任务中止", current_task(snapshot))
    recent_detail = activity_main(snapshot, 72)
    if "received token_count" not in recent_detail:
        add(snapshot.last_event_time, "最近事件", recent_detail)
    if not events:
        add(snapshot.refreshed_at, "刷新", "等待会话产生新活动")

    return renderer_activity.merge_activity_events(
        events,
        compact=context.compact,
        timeline_time_fn=context.timeline_time,
    )


__all__ = ["ActivityTrailContext", "activity_trail"]
