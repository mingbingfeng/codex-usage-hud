"""Core token estimation and billing logic."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

MODEL_PRICES: dict[str, dict[str, float]] = {
    "gpt-5.5": {
        "input": 5.00,
        "cached_input": 0.50,
        "output": 30.00,
        "reasoning": 30.00,
    },
    "gpt-5.4": {
        "input": 2.50,
        "cached_input": 0.25,
        "output": 15.00,
        "reasoning": 15.00,
    },
    "gpt-5.4-mini": {
        "input": 0.75,
        "cached_input": 0.075,
        "output": 4.50,
        "reasoning": 4.50,
    },
}


def estimate_tokens(text: Any) -> int:
    """Estimate token count using the legacy HUD heuristic."""
    if text is None:
        return 0
    value = str(text)
    if not value:
        return 0

    ascii_chars = sum(1 for char in value if ord(char) < 128)
    non_ascii_chars = len(value) - ascii_chars
    return int(math.ceil((ascii_chars / 4.0) + (non_ascii_chars / 1.6)))


class UsageCalculator:
    """Calculate usage cost from token counts and per-model pricing."""

    def __init__(
        self, model_prices: Mapping[str, Mapping[str, float]] | None = None
    ) -> None:
        source = MODEL_PRICES if model_prices is None else model_prices
        self._model_prices = {name: dict(prices) for name, prices in source.items()}

    def normalize_model_name(self, model_name: str) -> str:
        """Resolve a model name to the closest supported price entry."""
        normalized = (model_name or "").strip().lower()
        normalized = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", normalized)
        for name in sorted(self._model_prices, key=len, reverse=True):
            if normalized == name or normalized.startswith(name + "-"):
                return name
        return normalized

    def calculate_cost_usd(
        self,
        model_name: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
    ) -> float:
        """Calculate total USD cost rounded to 6 decimal places."""
        normalized_model = self.normalize_model_name(model_name)
        prices = self._model_prices.get(normalized_model)
        if prices is None:
            raise ValueError(f"Unsupported model: {model_name}")

        total_input_tokens = max(0, int(input_tokens or 0))
        cached_tokens = max(0, min(int(cached_input_tokens or 0), total_input_tokens))
        uncached_tokens = total_input_tokens - cached_tokens
        output_count = max(0, int(output_tokens or 0))
        reasoning_count = max(0, int(reasoning_tokens or 0))

        total_cost = (
            (uncached_tokens * prices["input"])
            + (cached_tokens * prices["cached_input"])
            + (output_count * prices["output"])
            + (reasoning_count * prices["reasoning"])
        ) / 1_000_000.0
        return round(total_cost, 6)
