"""Pure budget progress and overflow presentation helpers."""

from __future__ import annotations

from typing import Callable

from .common import format_money


RatioCalculator = Callable[[float | None, float | None], float]
MoneyFormatter = Callable[[float | None], str]
OverflowPartsFormatter = Callable[[float | None, float | None], tuple[str, str]]


def progress_total_ratio(cost: float | None, limit: float | None) -> float:
    amount = max(0.0, float(cost or 0.0))
    budget = max(0.0, float(limit or 0.0))
    if budget <= 0.0:
        return 0.0
    return max(0.0, amount / budget)


def progress_ratio(
    cost: float | None,
    limit: float | None,
    *,
    total_ratio: RatioCalculator = progress_total_ratio,
) -> float:
    return max(0.0, min(1.0, total_ratio(cost, limit)))


def progress_total_text(
    cost: float | None,
    limit: float | None,
    *,
    total_ratio: RatioCalculator = progress_total_ratio,
) -> str:
    total = total_ratio(cost, limit)
    if total <= 0.0:
        return ""
    return f"{total:.0%}"


def progress_overflow_ratio(
    cost: float | None,
    limit: float | None,
    *,
    total_ratio: RatioCalculator = progress_total_ratio,
) -> float:
    total = total_ratio(cost, limit)
    return max(0.0, min(1.0, total - 1.0))


def progress_overflow_parts(
    cost: float | None,
    limit: float | None,
    *,
    total_ratio: RatioCalculator = progress_total_ratio,
    money_formatter: MoneyFormatter = format_money,
) -> tuple[str, str]:
    total = total_ratio(cost, limit)
    if total <= 1.0:
        return "", ""
    amount = max(0.0, float(cost or 0.0))
    budget = max(0.0, float(limit or 0.0))
    overflow_ratio = max(0.0, total - 1.0)
    overflow_cost = max(0.0, amount - budget)
    percent = f"+{overflow_ratio:.0%}"
    money = f"+{money_formatter(overflow_cost)}"
    return f"{percent} / {money}", money


def progress_overflow_badge(
    cost: float | None,
    limit: float | None,
    *,
    overflow_parts: OverflowPartsFormatter = progress_overflow_parts,
) -> str:
    full, _compact = overflow_parts(cost, limit)
    return full


def progress_overflow_badge_compact(
    cost: float | None,
    limit: float | None,
    *,
    overflow_parts: OverflowPartsFormatter = progress_overflow_parts,
) -> str:
    _full, compact = overflow_parts(cost, limit)
    return compact


def limit_text(limit: float | None, *, money_formatter: MoneyFormatter = format_money) -> str:
    return f"总 {money_formatter(limit)}"


def progress_metric(
    label: str,
    ratio: float | None,
    tone: str,
    *,
    right_text: str = "",
    overflow_ratio: float | None = None,
    overflow_badge: str = "",
    overflow_badge_compact: str = "",
    overflow_badge_icon: str = "",
) -> dict[str, object]:
    metric: dict[str, object] = {
        "label": label,
        "ratio": max(0.0, min(1.0, float(ratio or 0.0))),
        "tone": tone,
    }
    if right_text:
        metric["rightText"] = right_text
    if overflow_ratio is not None and float(overflow_ratio) > 0.0:
        metric["overflowRatio"] = max(0.0, min(1.0, float(overflow_ratio)))
    if overflow_badge:
        metric["overflowBadge"] = overflow_badge
    if overflow_badge_compact:
        metric["overflowBadgeCompact"] = overflow_badge_compact
    if overflow_badge_icon:
        metric["overflowBadgeIcon"] = overflow_badge_icon
    return metric


__all__ = [
    "limit_text",
    "progress_metric",
    "progress_overflow_badge",
    "progress_overflow_badge_compact",
    "progress_overflow_parts",
    "progress_overflow_ratio",
    "progress_ratio",
    "progress_total_ratio",
    "progress_total_text",
]
