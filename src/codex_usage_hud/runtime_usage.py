"""Usage arithmetic shared by runtime caches and overlay projections."""

from __future__ import annotations

from datetime import datetime
import re

from .core import ParsedSession, RequestRound, UsageSummary


def format_money(value: float | None) -> str:
    return f"${float(value or 0.0):,.6f}"


def format_tokens(value: int | None) -> str:
    amount = int(value or 0)
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.1f}k"
    return f"{amount}"


def format_cost_compact(value: float | None) -> str:
    amount = float(value or 0.0)
    if amount <= 0:
        return "$0"
    if amount >= 0.01:
        return f"${amount:.2f}"
    return f"${amount:.3f}".rstrip("0").rstrip(".")


def request_round_from_snapshot(snapshot: ParsedSession) -> RequestRound | None:
    request = snapshot.request
    if not (
        request.total_tokens
        or request.input_tokens is not None
        or request.output_tokens is not None
        or request.cost_usd is not None
    ):
        return None
    total_tokens = request.total_tokens or (
        int(request.input_tokens or 0) + int(request.output_tokens or 0)
    )
    return RequestRound(
        index=max(1, int(request.round_index or 1)),
        status=request.status,
        model=request.model,
        input_tokens=request.input_tokens,
        cached_tokens=request.cached_tokens,
        cache_write_tokens=request.cache_write_tokens,
        output_tokens=request.output_tokens,
        reasoning_tokens=request.reasoning_tokens,
        total_tokens=total_tokens,
        estimated=request.estimated,
        cost_usd=request.cost_usd,
        started_at=request.started_at,
        completed_at=request.completed_at,
    )


def current_task_rounds(snapshot: ParsedSession) -> list[RequestRound]:
    if snapshot.request_history:
        return list(snapshot.request_history)
    current = request_round_from_snapshot(snapshot)
    return [current] if current is not None else []


def current_task_usage(snapshot: ParsedSession) -> tuple[int, float | None]:
    rows = current_task_rounds(snapshot)
    if not rows:
        if snapshot.estimate.total_tokens:
            return int(snapshot.estimate.total_tokens), None
        return int(snapshot.confirmed.last_total or 0), None

    total_tokens = 0
    cost = 0.0
    has_cost = False
    for index, item in enumerate(rows):
        item_total = item.total_tokens or (
            int(item.input_tokens or 0) + int(item.output_tokens or 0)
        )
        total_tokens += int(item_total or 0)
        if item.cost_usd is not None:
            cost += float(item.cost_usd)
            has_cost = True
        elif index == len(rows) - 1 and snapshot.request.cost_usd is not None:
            cost += float(snapshot.request.cost_usd)
            has_cost = True
    return total_tokens, (round(cost, 6) if has_cost else None)


def workdir_leaf(value: object) -> str:
    text = str(value or "").strip().rstrip("\\/")
    if not text:
        return ""
    parts = [part for part in re.split(r"[\\/]+", text) if part]
    return parts[-1] if parts else text


def current_session_cost(snapshot: ParsedSession) -> float:
    confirmed_cost = float(snapshot.confirmed.cumulative_cost_usd or 0.0)
    pending_cost = 0.0
    if snapshot.request.status == "running" and snapshot.request.cost_usd is not None:
        pending_cost = float(snapshot.request.cost_usd)
    return round(confirmed_cost + pending_cost, 6)


def merge_usage(target: UsageSummary, addition: UsageSummary) -> None:
    target.tokens += addition.tokens
    target.input_tokens += addition.input_tokens
    target.cached_tokens += addition.cached_tokens
    target.cache_write_tokens += addition.cache_write_tokens
    target.output_tokens += addition.output_tokens
    target.reasoning_tokens += addition.reasoning_tokens
    target.cost_usd = round(target.cost_usd + addition.cost_usd, 6)


def replace_usage(
    total: UsageSummary, old: UsageSummary, new: UsageSummary
) -> UsageSummary:
    return UsageSummary(
        tokens=max(0, total.tokens - old.tokens + new.tokens),
        input_tokens=max(0, total.input_tokens - old.input_tokens + new.input_tokens),
        cached_tokens=max(0, total.cached_tokens - old.cached_tokens + new.cached_tokens),
        cache_write_tokens=max(
            0, total.cache_write_tokens - old.cache_write_tokens + new.cache_write_tokens
        ),
        output_tokens=max(0, total.output_tokens - old.output_tokens + new.output_tokens),
        reasoning_tokens=max(
            0, total.reasoning_tokens - old.reasoning_tokens + new.reasoning_tokens
        ),
        cost_usd=round(max(0.0, total.cost_usd - old.cost_usd + new.cost_usd), 6),
    )


def usage_before_today_in_week(
    week_total: UsageSummary,
    today_total: UsageSummary,
    day_start: datetime,
    week_start: datetime,
) -> UsageSummary:
    if day_start <= week_start:
        return UsageSummary()
    return replace_usage(week_total, today_total, UsageSummary())


_current_task_usage = current_task_usage
_current_task_rounds = current_task_rounds


def _current_task_tokens(snapshot: ParsedSession) -> int:
    tokens, _cost = _current_task_usage(snapshot)
    return tokens


def _current_task_cost(snapshot: ParsedSession) -> float | None:
    _tokens, cost = _current_task_usage(snapshot)
    return cost


def _current_task_cache_hit_text(snapshot: ParsedSession) -> str:
    rows = _current_task_rounds(snapshot)
    if rows:
        input_total = 0
        cached_total = 0
        for item in rows:
            input_amount = int(item.input_tokens or 0)
            if input_amount <= 0:
                continue
            cached_amount = max(0, min(int(item.cached_tokens or 0), input_amount))
            input_total += input_amount
            cached_total += cached_amount
        if input_total > 0:
            return f"{round((cached_total / max(1, input_total)) * 100):.0f}%"

    request = snapshot.request
    input_tokens = request.input_tokens
    cached_tokens = request.cached_tokens
    if input_tokens is None or int(input_tokens or 0) <= 0:
        input_tokens = snapshot.confirmed.last_input
        cached_tokens = snapshot.confirmed.last_cached
    input_amount = int(input_tokens or 0)
    if input_amount <= 0:
        return "--"
    cached_amount = max(0, min(int(cached_tokens or 0), input_amount))
    return f"{round((cached_amount / max(1, input_amount)) * 100):.0f}%"


def _current_task_round_index(snapshot: ParsedSession) -> int:
    round_index = max(0, int(snapshot.request.round_index or 0))
    rows = _current_task_rounds(snapshot)
    if rows:
        round_index = max(round_index, int(rows[-1].index or 0))
    return round_index


def _current_task_model_name(snapshot: ParsedSession) -> str:
    model_name = str(snapshot.request.model or "").strip()
    if model_name:
        return model_name
    for item in reversed(_current_task_rounds(snapshot)):
        model_name = str(item.model or "").strip()
        if model_name:
            return model_name
    return ""
