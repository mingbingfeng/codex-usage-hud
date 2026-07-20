"""Core token estimation and billing logic."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import math
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

MODEL_PRICES: dict[str, dict[str, float]] = {
    "gpt-5.6-sol": {
        "input": 5.00,
        "cached_input": 0.50,
        "cache_write": 6.25,
        "output": 30.00,
        "reasoning": 30.00,
    },
    "gpt-5.6-terra": {
        "input": 2.50,
        "cached_input": 0.25,
        "cache_write": 3.125,
        "output": 15.00,
        "reasoning": 15.00,
    },
    "gpt-5.6-luna": {
        "input": 1.00,
        "cached_input": 0.10,
        "cache_write": 1.25,
        "output": 6.00,
        "reasoning": 6.00,
    },
    "gpt-5.5": {
        "input": 5.00,
        "cached_input": 0.50,
        "cache_write": 0.00,
        "output": 30.00,
        "reasoning": 30.00,
    },
    "gpt-5.4": {
        "input": 2.50,
        "cached_input": 0.25,
        "cache_write": 0.00,
        "output": 15.00,
        "reasoning": 15.00,
    },
    "gpt-5.4-mini": {
        "input": 0.75,
        "cached_input": 0.075,
        "cache_write": 0.00,
        "output": 4.50,
        "reasoning": 4.50,
    },
}


REQUIRED_PRICE_FIELDS = ("input", "cached_input", "output")


@dataclass(frozen=True)
class _PriceProfile:
    key: str
    model_pattern: str
    provider: str
    base_url: str
    prices: dict[str, float]


def _normalize_model_text(value: str) -> str:
    normalized = (value or "").strip().lower()
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", normalized)


def _normalize_provider(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_base_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        return text.rstrip("/").lower()
    path = parts.path.rstrip("/")
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            "",
            "",
        )
    )


def _model_matches(pattern: str, model_name: str) -> tuple[bool, int]:
    normalized_pattern = _normalize_model_text(pattern)
    normalized_model = _normalize_model_text(model_name)
    if not normalized_pattern:
        return False, 0
    if "*" in normalized_pattern or "?" in normalized_pattern:
        if fnmatchcase(normalized_model, normalized_pattern):
            return True, 70 + len(normalized_pattern.replace("*", "").replace("?", ""))
        return False, 0
    if normalized_model == normalized_pattern:
        return True, 100 + len(normalized_pattern)
    if normalized_model.startswith(normalized_pattern + "-"):
        return True, 60 + len(normalized_pattern)
    return False, 0


def _price_profile_from_mapping(key: str, value: Mapping[str, Any]) -> _PriceProfile:
    prices = {
        "input": float(value["input"]),
        "cached_input": float(value["cached_input"]),
        "cache_write": float(value.get("cache_write", 0.0)),
        "output": float(value["output"]),
        # Kept in the profile for settings compatibility. Codex output_tokens
        # already includes reasoning_output_tokens, so it is not billed twice.
        "reasoning": float(value.get("reasoning", value["output"])),
    }
    model_pattern = str(
        value.get("model")
        or value.get("model_pattern")
        or value.get("pattern")
        or key
    ).strip()
    provider = _normalize_provider(value.get("provider"))
    base_url = _normalize_base_url(
        value.get("base_url") or value.get("baseUrl") or value.get("api_base")
    )
    return _PriceProfile(
        key=str(key),
        model_pattern=model_pattern,
        provider=provider,
        base_url=base_url,
        prices=prices,
    )


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
        self, model_prices: Mapping[str, Mapping[str, Any]] | None = None
    ) -> None:
        source = MODEL_PRICES if model_prices is None else model_prices
        self._model_prices = {name: dict(prices) for name, prices in source.items()}
        self._price_profiles = [
            _price_profile_from_mapping(name, prices)
            for name, prices in self._model_prices.items()
            if all(field in prices for field in REQUIRED_PRICE_FIELDS)
        ]

    def normalize_model_name(self, model_name: str) -> str:
        """Resolve a model name to the closest supported price entry."""
        profile = self._resolve_price_profile(model_name)
        if profile is not None:
            return profile.key
        normalized = _normalize_model_text(model_name)
        for profile in sorted(
            self._price_profiles,
            key=lambda item: len(item.model_pattern),
            reverse=True,
        ):
            if profile.provider or profile.base_url:
                continue
            matches, _score = _model_matches(profile.model_pattern, normalized)
            if matches:
                return profile.key
        return normalized

    def _resolve_price_profile(
        self,
        model_name: str,
        *,
        provider: str = "",
        base_url: str = "",
    ) -> _PriceProfile | None:
        normalized_provider = _normalize_provider(provider)
        normalized_base_url = _normalize_base_url(base_url)
        candidates: list[tuple[int, _PriceProfile]] = []
        for profile in self._price_profiles:
            matches, score = _model_matches(profile.model_pattern, model_name)
            if not matches:
                continue
            if normalized_provider:
                if profile.provider and profile.provider != normalized_provider:
                    continue
                if profile.provider == normalized_provider:
                    score += 1000
            elif profile.provider:
                score -= 200
            if normalized_base_url:
                if profile.base_url and not normalized_base_url.startswith(profile.base_url):
                    continue
                if profile.base_url:
                    score += 2000 + len(profile.base_url)
            elif profile.base_url:
                score -= 400
            candidates.append((score, profile))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def price_snapshot(
        self,
        model_name: str,
        *,
        provider: str = "",
        base_url: str = "",
    ) -> dict[str, object] | None:
        """Return the exact local price profile selected for an estimate."""
        profile = self._resolve_price_profile(
            model_name,
            provider=provider,
            base_url=base_url,
        )
        if profile is None:
            return None
        return {
            "key": profile.key,
            "model": profile.model_pattern,
            "provider": profile.provider or _normalize_provider(provider),
            "baseUrl": profile.base_url or _normalize_base_url(base_url),
            "prices": dict(profile.prices),
        }

    def calculate_cost_usd(
        self,
        model_name: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
        *,
        cache_write_tokens: int = 0,
        provider: str = "",
        base_url: str = "",
    ) -> float:
        """Calculate total USD cost rounded to 6 decimal places."""
        profile = self._resolve_price_profile(
            model_name,
            provider=provider,
            base_url=base_url,
        )
        if profile is None:
            raise ValueError(f"Unsupported model: {model_name}")
        prices = profile.prices

        total_input_tokens = max(0, int(input_tokens or 0))
        cached_tokens = max(0, min(int(cached_input_tokens or 0), total_input_tokens))
        cache_write_count = max(
            0,
            min(int(cache_write_tokens or 0), total_input_tokens - cached_tokens),
        )
        uncached_tokens = total_input_tokens - cached_tokens - cache_write_count
        output_count = max(0, int(output_tokens or 0))
        del reasoning_tokens

        total_cost = (
            (uncached_tokens * prices["input"])
            + (cached_tokens * prices["cached_input"])
            + (cache_write_count * prices["cache_write"])
            + (output_count * prices["output"])
        ) / 1_000_000.0
        return round(total_cost, 6)
