"""Pure request/task-round projection for Renderer payloads.

This owner converts a parsed session into request rows, row details, and task
totals.  It owns no payload envelope or runtime state; model/session decisions
and formatting dependencies arrive through ``RequestProjectionContext``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .core.parser import CostEstimator, ParsedSession, RequestRound
from .renderer_presenters import request as renderer_request


DisplayTokens = tuple[
    int | None,
    bool,
    int | None,
    bool,
    int | None,
    bool,
    int | None,
    bool,
]
DisplayTokensFn = Callable[[ParsedSession], DisplayTokens]
SessionFlagFn = Callable[[ParsedSession], bool]
RateValueFn = Callable[[float | None, bool], str]
MoneyFn = Callable[[float | None, bool], str]
FixedTotalFn = Callable[[int | None], str]
ShortNumberFn = Callable[[int | None], str]


@dataclass(frozen=True)
class RequestProjectionContext:
    """Snapshot and formatting callbacks required by this owner."""

    cost_estimator: CostEstimator
    is_new_session: SessionFlagFn
    is_pending_session: SessionFlagFn
    display_tokens: DisplayTokensFn
    format_rate_value: RateValueFn
    format_fixed_money: MoneyFn
    fixed_token_total: FixedTotalFn
    short_number: ShortNumberFn


def round_cache_hit_rate_value(
    item: RequestRound,
    *,
    context: RequestProjectionContext,
) -> str:
    return renderer_request.round_cache_hit_rate_value(
        item,
        rate_value=context.format_rate_value,
    )


def request_cost(
    snapshot: ParsedSession,
    *,
    context: RequestProjectionContext,
) -> tuple[float | None, bool]:
    request = snapshot.request
    if request.cost_usd is not None and not request.estimated:
        return request.cost_usd, False
    input_tokens = request.input_tokens
    cached_tokens = request.cached_tokens
    cache_write_tokens = request.cache_write_tokens
    output_tokens = request.output_tokens or 0
    if input_tokens is None or request.estimated:
        input_tokens = max(
            int(input_tokens or 0),
            snapshot.confirmed.last_input
            + snapshot.estimate.input_tokens
            + snapshot.estimate.tool_tokens,
        )
        cached_tokens = min(snapshot.confirmed.last_cached, int(input_tokens or 0))
        cache_write_tokens = min(
            snapshot.confirmed.last_cache_write,
            max(0, int(input_tokens or 0) - int(cached_tokens or 0)),
        )
    cost = context.cost_estimator.calculate(
        request.model,
        input_tokens,
        cached_tokens,
        output_tokens,
        request.reasoning_tokens or 0,
        cache_write_tokens=cache_write_tokens or 0,
    )
    return cost, True


def round_from_snapshot(
    snapshot: ParsedSession,
    *,
    context: RequestProjectionContext,
) -> RequestRound:
    (
        input_tokens,
        _input_estimated,
        output_tokens,
        _output_estimated,
        reasoning_tokens,
        _reasoning_estimated,
        total_tokens,
        total_estimated,
    ) = context.display_tokens(snapshot)
    cost, cost_estimated = request_cost(snapshot, context=context)
    return RequestRound(
        index=1,
        status=snapshot.request.status,
        model=snapshot.request.model,
        input_tokens=input_tokens,
        cached_tokens=(
            snapshot.request.cached_tokens
            if snapshot.request.cached_tokens is not None
            else min(snapshot.confirmed.last_cached, int(input_tokens or 0))
        ),
        cache_write_tokens=(
            snapshot.request.cache_write_tokens
            if snapshot.request.cache_write_tokens is not None
            else min(
                snapshot.confirmed.last_cache_write,
                max(
                    0,
                    int(input_tokens or 0)
                    - int(snapshot.request.cached_tokens or snapshot.confirmed.last_cached or 0),
                ),
            )
        ),
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        estimated=snapshot.request.estimated or total_estimated or cost_estimated,
        cost_usd=cost,
        started_at=snapshot.request.started_at,
        completed_at=snapshot.request.completed_at,
    )


def task_rows(
    snapshot: ParsedSession,
    *,
    context: RequestProjectionContext,
) -> list[RequestRound]:
    if context.is_new_session(snapshot) or context.is_pending_session(snapshot):
        return []
    return snapshot.request_history or [round_from_snapshot(snapshot, context=context)]


def task_total(
    snapshot: ParsedSession,
    *,
    context: RequestProjectionContext,
) -> tuple[int, int, int, int, int, float | None, bool]:
    rows = task_rows(snapshot, context=context)
    input_tokens = sum(int(item.input_tokens or 0) for item in rows)
    cached_tokens = sum(int(item.cached_tokens or 0) for item in rows)
    output_tokens = sum(int(item.output_tokens or 0) for item in rows)
    reasoning_tokens = sum(int(item.reasoning_tokens or 0) for item in rows)
    total_tokens = sum(int(item.total_tokens or 0) for item in rows)
    if not total_tokens:
        total_tokens = input_tokens + output_tokens
    cost = 0.0
    has_cost = False
    estimated = False
    for item in rows:
        item_cost = item.cost_usd
        item_estimated = item.estimated or item.status == "running"
        if item_cost is None:
            item_cost = context.cost_estimator.calculate(
                item.model or snapshot.request.model,
                item.input_tokens or 0,
                item.cached_tokens or 0,
                item.output_tokens or 0,
                item.reasoning_tokens or 0,
                cache_write_tokens=item.cache_write_tokens or 0,
            )
            item_estimated = True
        if item_cost is not None:
            cost += item_cost
            has_cost = True
        estimated = estimated or item_estimated
    return (
        input_tokens,
        cached_tokens,
        output_tokens,
        reasoning_tokens,
        total_tokens,
        cost if has_cost else None,
        estimated,
    )


def round_is_running(item: RequestRound) -> bool:
    return renderer_request.round_is_running(item)


def round_elapsed_text(
    started_at: datetime | None,
    *,
    now: datetime | None = None,
) -> str:
    return renderer_request.round_elapsed_text(started_at, now=now)


def round_time_text(
    item: RequestRound,
    *,
    now: datetime | None = None,
) -> str:
    return renderer_request.round_time_text(
        item,
        now=now,
        is_running=round_is_running,
        elapsed_text=round_elapsed_text,
    )


def round_time_iso(value: datetime | None) -> str:
    return renderer_request.round_time_iso(value)


def round_entry_parts(
    item: RequestRound,
    fallback_model: str,
    *,
    context: RequestProjectionContext,
    widths: renderer_request.RoundColumnWidths | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    return renderer_request.round_entry_parts(
        item,
        fallback_model,
        cost_estimator=context.cost_estimator,
        widths=widths,
        now=now,
        time_text=round_time_text,
        cache_rate_value=lambda value: round_cache_hit_rate_value(
            value,
            context=context,
        ),
        fixed_money=context.format_fixed_money,
        fixed_total=context.fixed_token_total,
        short_number=context.short_number,
    )


def round_entry(
    item: RequestRound,
    fallback_model: str,
    *,
    context: RequestProjectionContext,
    widths: renderer_request.RoundColumnWidths | None = None,
    now: datetime | None = None,
) -> str:
    return renderer_request.round_entry(
        item,
        fallback_model,
        widths=widths,
        now=now,
        entry_parts=lambda value, model, *, widths=None, now=None: round_entry_parts(
            value,
            model,
            context=context,
            widths=widths,
            now=now,
        ),
    )


def round_entry_widths(
    rows: list[RequestRound],
    fallback_model: str,
    *,
    context: RequestProjectionContext,
) -> renderer_request.RoundColumnWidths:
    return renderer_request.round_entry_widths(
        rows,
        fallback_model,
        cost_estimator=context.cost_estimator,
        fixed_money=context.format_fixed_money,
        fixed_total=context.fixed_token_total,
        short_number=context.short_number,
        cache_rate_value=lambda value: round_cache_hit_rate_value(
            value,
            context=context,
        ),
    )


def display_request_rows(
    snapshot: ParsedSession,
    *,
    context: RequestProjectionContext,
) -> tuple[list[RequestRound], renderer_request.RoundColumnWidths]:
    if context.is_new_session(snapshot) or context.is_pending_session(snapshot):
        return [], renderer_request.RoundColumnWidths()
    rows = task_rows(snapshot, context=context)[-30:]
    if not rows:
        rows = [round_from_snapshot(snapshot, context=context)]
    display_rows = list(reversed(rows))
    widths = round_entry_widths(
        display_rows,
        snapshot.request.model,
        context=context,
    )
    return display_rows, widths


def request_rows(
    snapshot: ParsedSession,
    *,
    context: RequestProjectionContext,
) -> list[str]:
    display_rows, widths = display_request_rows(snapshot, context=context)
    return [
        round_entry(
            item,
            snapshot.request.model,
            context=context,
            widths=widths,
        )
        for item in display_rows
    ]


def request_row_details(
    snapshot: ParsedSession,
    *,
    context: RequestProjectionContext,
) -> list[dict[str, object]]:
    display_rows, widths = display_request_rows(snapshot, context=context)
    details: list[dict[str, object]] = []
    for item in display_rows:
        parts = round_entry_parts(
            item,
            snapshot.request.model,
            context=context,
            widths=widths,
        )
        details.append(
            {
                "text": f"{parts['prefix']}{parts['time']}{parts['suffix']}",
                "prefix": parts["prefix"],
                "time": parts["time"],
                "suffix": parts["suffix"],
                "running": round_is_running(item),
                "startedAt": round_time_iso(item.started_at),
                "completedAt": round_time_iso(item.completed_at),
            }
        )
    return details


RoundColumnWidths = renderer_request.RoundColumnWidths


__all__ = [
    "RequestProjectionContext",
    "RoundColumnWidths",
    "display_request_rows",
    "request_cost",
    "request_row_details",
    "request_rows",
    "round_cache_hit_rate_value",
    "round_elapsed_text",
    "round_entry",
    "round_entry_parts",
    "round_entry_widths",
    "round_from_snapshot",
    "round_is_running",
    "round_time_iso",
    "round_time_text",
    "task_rows",
    "task_total",
]
