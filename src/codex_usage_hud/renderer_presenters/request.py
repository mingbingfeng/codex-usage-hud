"""Request-round timing, column sizing, and row presentation helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, NamedTuple

from ..core.parser import CostEstimator, RequestRound
from .common import (
    fixed_token_total,
    format_fixed_money,
    format_rate_value,
    short_num,
)


class RoundColumnWidths(NamedTuple):
    index: int = 1
    money: int = 1
    total: int = 1
    input: int = 1
    rate: int = 1
    output: int = 1
    reasoning: int = 1
    cached: int = 1


RoundIsRunningFn = Callable[[RequestRound], bool]
RoundElapsedFn = Callable[[datetime | None], str]
RoundTimeFn = Callable[[RequestRound], str]
RoundCacheRateFn = Callable[[RequestRound], str]
FixedMoneyFn = Callable[[float | None, bool], str]
ShortNumberFn = Callable[[int | None], str]
FixedTokenTotalFn = Callable[[int | None], str]


def round_cache_hit_rate_value(
    item: RequestRound,
    *,
    rate_value: Callable[[float | None, bool], str] = format_rate_value,
) -> str:
    input_tokens = item.input_tokens
    if input_tokens is None or int(input_tokens) <= 0:
        return rate_value(None, item.estimated)
    cached_tokens = max(0, min(int(item.cached_tokens or 0), int(input_tokens)))
    return rate_value(cached_tokens / max(1, int(input_tokens)), item.estimated)


def round_is_running(item: RequestRound) -> bool:
    return item.status == "running" and item.completed_at is None and item.started_at is not None


def round_elapsed_text(
    started_at: datetime | None,
    *,
    now: datetime | None = None,
) -> str:
    if started_at is None:
        return "--:--:--"
    if started_at.tzinfo is None:
        current = (now or datetime.now()).replace(tzinfo=None)
    else:
        current = (now or datetime.now().astimezone()).astimezone(started_at.tzinfo)
    elapsed_seconds = max(0, int((current - started_at).total_seconds()))
    return f"{elapsed_seconds}s".rjust(8)


def round_time_text(
    item: RequestRound,
    *,
    now: datetime | None = None,
    is_running: RoundIsRunningFn = round_is_running,
    elapsed_text: Callable[..., str] = round_elapsed_text,
) -> str:
    if is_running(item):
        return elapsed_text(item.started_at, now=now)
    time_source = item.completed_at or item.started_at
    return "--:--:--" if time_source is None else time_source.astimezone().strftime("%H:%M:%S")


def round_time_iso(value: datetime | None) -> str:
    return "" if value is None else value.astimezone().isoformat()


def round_entry(
    item: RequestRound,
    fallback_model: str,
    *,
    widths: RoundColumnWidths | None = None,
    now: datetime | None = None,
    entry_parts: Callable[..., dict[str, str]],
) -> str:
    parts = entry_parts(
        item,
        fallback_model,
        widths=widths,
        now=now,
    )
    return f"{parts['prefix']}{parts['time']}{parts['suffix']}"


def round_entry_parts(
    item: RequestRound,
    fallback_model: str,
    *,
    cost_estimator: CostEstimator,
    widths: RoundColumnWidths | None = None,
    now: datetime | None = None,
    time_text: RoundTimeFn = round_time_text,
    cache_rate_value: RoundCacheRateFn = round_cache_hit_rate_value,
    fixed_money: FixedMoneyFn = format_fixed_money,
    fixed_total: FixedTokenTotalFn = fixed_token_total,
    short_number: ShortNumberFn = short_num,
) -> dict[str, str]:
    cost = item.cost_usd
    estimated = item.estimated or cost is None
    if cost is None:
        cost = cost_estimator.calculate(
            item.model or fallback_model,
            item.input_tokens or 0,
            item.cached_tokens or 0,
            item.output_tokens or 0,
            item.reasoning_tokens or 0,
            cache_write_tokens=item.cache_write_tokens or 0,
        )
    time_value = time_text(item, now=now)
    index_text = str(item.index)
    money_text = fixed_money(cost, estimated)
    total_text = fixed_total(item.total_tokens)
    input_text = short_number(item.input_tokens)
    rate_text = cache_rate_value(item)
    output_text = short_number(item.output_tokens)
    reasoning_text = short_number(item.reasoning_tokens)
    cached_text = short_number(item.cached_tokens)
    if widths is not None:
        index_text = index_text.rjust(widths.index)
        money_text = money_text.rjust(widths.money)
        total_text = total_text.rjust(widths.total)
        input_text = input_text.rjust(widths.input)
        rate_text = rate_text.rjust(widths.rate)
        output_text = output_text.rjust(widths.output)
        reasoning_text = reasoning_text.rjust(widths.reasoning)
        cached_text = cached_text.rjust(widths.cached)
    return {
        "prefix": f"#{index_text} {money_text} ",
        "time": time_value,
        "suffix": (
            f" ↑{input_text} ◎{rate_text} "
            f"↓{output_text} ◇{reasoning_text} "
            f"↻{cached_text} ∑{total_text}"
        ),
    }


def round_entry_widths(
    rows: list[RequestRound],
    fallback_model: str,
    *,
    cost_estimator: CostEstimator,
    fixed_money: FixedMoneyFn = format_fixed_money,
    fixed_total: FixedTokenTotalFn = fixed_token_total,
    short_number: ShortNumberFn = short_num,
    cache_rate_value: RoundCacheRateFn = round_cache_hit_rate_value,
) -> RoundColumnWidths:
    index_width = max((len(str(item.index)) for item in rows), default=1)
    money_width = 1
    total_width = 1
    input_width = 1
    rate_width = 1
    output_width = 1
    reasoning_width = 1
    cached_width = 1
    for item in rows:
        cost = item.cost_usd
        estimated = item.estimated or cost is None
        if cost is None:
            cost = cost_estimator.calculate(
                item.model or fallback_model,
                item.input_tokens or 0,
                item.cached_tokens or 0,
                item.output_tokens or 0,
                item.reasoning_tokens or 0,
                cache_write_tokens=item.cache_write_tokens or 0,
            )
        money_width = max(money_width, len(fixed_money(cost, estimated)))
        total_width = max(total_width, len(fixed_total(item.total_tokens)))
        input_width = max(input_width, len(short_number(item.input_tokens)))
        rate_width = max(rate_width, len(cache_rate_value(item)))
        output_width = max(output_width, len(short_number(item.output_tokens)))
        reasoning_width = max(reasoning_width, len(short_number(item.reasoning_tokens)))
        cached_width = max(cached_width, len(short_number(item.cached_tokens)))
    return RoundColumnWidths(
        index=index_width,
        money=money_width,
        total=total_width,
        input=input_width,
        rate=rate_width,
        output=output_width,
        reasoning=reasoning_width,
        cached=cached_width,
    )


__all__ = [
    "RoundColumnWidths",
    "round_cache_hit_rate_value",
    "round_elapsed_text",
    "round_entry",
    "round_entry_parts",
    "round_entry_widths",
    "round_is_running",
    "round_time_iso",
    "round_time_text",
]
