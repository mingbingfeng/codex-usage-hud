"""User-facing configuration for codex-usage-hud."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping
from urllib.error import URLError
from urllib.request import Request, urlopen

from .core.calculator import MODEL_PRICES

HUD_SETTINGS_FILENAME = "hud_settings.json"
USER_CONFIG_KEY = "user"
DEFAULT_DAILY_BUDGET_USD = 100.0
DEFAULT_WEEKLY_BUDGET_USD = 400.0
DEFAULT_BUDGET_THRESHOLDS = (0.5, 0.8, 0.9, 1.0)
DEFAULT_DAILY_RESET_TIME = "10:00"
DEFAULT_WEEKLY_RESET_WEEKDAY = 3
DEFAULT_WEEKLY_RESET_TIME = "10:00"
DEFAULT_DISPLAY_MODE = "auto"
VALID_DISPLAY_MODES = {"auto", "renderer", "tk"}
DEFAULT_SUPPORT_URL = "https://github.com/mingbingfeng/codex-usage-hud"

_PRICE_ALIASES = {
    "input": ("input", "prompt", "input_price", "input_per_million"),
    "cached_input": (
        "cached_input",
        "cached",
        "cache",
        "cache_read",
        "cachedInput",
        "cached_input_price",
        "cached_input_per_million",
    ),
    "output": ("output", "completion", "output_price", "output_per_million"),
    "reasoning": (
        "reasoning",
        "reasoning_output",
        "reasoning_price",
        "reasoning_per_million",
    ),
}


@dataclass
class ModelPrice:
    """Per-million-token pricing for one model."""

    input: float
    cached_input: float
    output: float
    reasoning: float

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelPrice | None":
        prices: dict[str, float] = {}
        for canonical, aliases in _PRICE_ALIASES.items():
            raw = _first_present(value, aliases)
            amount = _optional_float(raw)
            if amount is not None:
                prices[canonical] = max(0.0, amount)
        if "input" not in prices or "output" not in prices:
            return None
        cached_input = prices.get("cached_input", prices["input"])
        reasoning = prices.get("reasoning", prices["output"])
        return cls(
            input=prices["input"],
            cached_input=cached_input,
            output=prices["output"],
            reasoning=reasoning,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "input": float(self.input),
            "cached_input": float(self.cached_input),
            "output": float(self.output),
            "reasoning": float(self.reasoning),
        }


def default_model_prices() -> dict[str, ModelPrice]:
    """Return the built-in model price table as config dataclasses."""
    return {
        name: ModelPrice.from_mapping(values) or ModelPrice(
            input=0.0,
            cached_input=0.0,
            output=0.0,
            reasoning=0.0,
        )
        for name, values in MODEL_PRICES.items()
    }


@dataclass
class UserConfig:
    """All user-editable runtime settings."""

    daily_budget_usd: float = DEFAULT_DAILY_BUDGET_USD
    weekly_budget_usd: float = DEFAULT_WEEKLY_BUDGET_USD
    daily_reset_time: str = DEFAULT_DAILY_RESET_TIME
    weekly_reset_weekday: int = DEFAULT_WEEKLY_RESET_WEEKDAY
    weekly_reset_time: str = DEFAULT_WEEKLY_RESET_TIME
    display_mode: str = DEFAULT_DISPLAY_MODE
    model_prices: dict[str, ModelPrice] = field(default_factory=default_model_prices)
    pricing_url: str = ""
    budget_thresholds: list[float] = field(
        default_factory=lambda: list(DEFAULT_BUDGET_THRESHOLDS)
    )
    weekly_adjustment_usd: float = 0.0
    support_url: str = DEFAULT_SUPPORT_URL

    @classmethod
    def defaults(cls) -> "UserConfig":
        return cls()

    @classmethod
    def from_dict(cls, value: Any) -> "UserConfig":
        if not isinstance(value, Mapping):
            return cls.defaults()
        defaults = cls.defaults()
        prices = normalize_model_prices(value.get("model_prices"))
        if not prices:
            prices = defaults.model_prices
        return cls(
            daily_budget_usd=max(
                0.0,
                _optional_float(value.get("daily_budget_usd"))
                if value.get("daily_budget_usd") is not None
                else defaults.daily_budget_usd,
            ),
            weekly_budget_usd=max(
                0.0,
                _optional_float(value.get("weekly_budget_usd"))
                if value.get("weekly_budget_usd") is not None
                else defaults.weekly_budget_usd,
            ),
            daily_reset_time=normalize_time_text(
                value.get("daily_reset_time"), defaults.daily_reset_time
            ),
            weekly_reset_weekday=normalize_weekday(
                value.get("weekly_reset_weekday"), defaults.weekly_reset_weekday
            ),
            weekly_reset_time=normalize_time_text(
                value.get("weekly_reset_time"), defaults.weekly_reset_time
            ),
            display_mode=normalize_display_mode(value.get("display_mode")),
            model_prices=prices,
            pricing_url=_optional_str(value.get("pricing_url")) or "",
            budget_thresholds=parse_thresholds(
                value.get("budget_thresholds"), defaults.budget_thresholds
            ),
            weekly_adjustment_usd=max(
                0.0,
                _optional_float(value.get("weekly_adjustment_usd"))
                if value.get("weekly_adjustment_usd") is not None
                else defaults.weekly_adjustment_usd,
            ),
            support_url=_optional_str(value.get("support_url")) or DEFAULT_SUPPORT_URL,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "daily_budget_usd": float(self.daily_budget_usd),
            "weekly_budget_usd": float(self.weekly_budget_usd),
            "daily_reset_time": self.daily_reset_time,
            "weekly_reset_weekday": int(self.weekly_reset_weekday),
            "weekly_reset_time": self.weekly_reset_time,
            "display_mode": self.display_mode,
            "pricing_url": self.pricing_url,
            "budget_thresholds": list(self.budget_thresholds),
            "weekly_adjustment_usd": float(self.weekly_adjustment_usd),
            "support_url": self.support_url,
            "model_prices": {
                name: price.to_dict()
                for name, price in sorted(self.model_prices.items())
            },
        }

    def price_table(self) -> dict[str, dict[str, float]]:
        return {name: price.to_dict() for name, price in self.model_prices.items()}

    def with_price_updates(
        self,
        prices: Mapping[str, ModelPrice],
        *,
        pricing_url: str | None = None,
    ) -> "UserConfig":
        next_prices = dict(self.model_prices)
        next_prices.update(prices)
        return replace(
            self,
            model_prices=next_prices,
            pricing_url=self.pricing_url if pricing_url is None else pricing_url,
        )


class UserConfigStore:
    """Read and write user config while preserving HUD geometry keys."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_settings_path()

    def load(self) -> UserConfig:
        raw = read_json_object(self.path)
        return UserConfig.from_dict(raw.get(USER_CONFIG_KEY))

    def save(self, config: UserConfig) -> None:
        raw = read_json_object(self.path)
        raw[USER_CONFIG_KEY] = config.to_dict()
        write_json_object(self.path, raw)

    def mtime(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None


def default_settings_path() -> Path:
    """Return the per-user shared HUD settings path."""
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
        return base / "codex-usage-hud" / HUD_SETTINGS_FILENAME
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "codex-usage-hud"
            / HUD_SETTINGS_FILENAME
        )
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "codex-usage-hud" / HUD_SETTINGS_FILENAME


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_json_object(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(
        json.dumps(dict(value), indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    temp_path.replace(path)


def parse_thresholds(
    value: Any,
    default: list[float] | tuple[float, ...] | None = None,
) -> list[float]:
    """Parse budget warning thresholds from CSV, percent, fraction, or list."""
    source: list[Any]
    if isinstance(value, str):
        source = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        source = list(value)
    else:
        source = list(default or DEFAULT_BUDGET_THRESHOLDS)
    thresholds: list[float] = []
    for part in source:
        amount = _optional_float(part)
        if amount is None:
            continue
        if amount > 1:
            amount /= 100.0
        if amount > 0:
            thresholds.append(amount)
    return sorted(set(thresholds)) or list(default or DEFAULT_BUDGET_THRESHOLDS)


def normalize_model_prices(value: Any) -> dict[str, ModelPrice]:
    """Normalize a model price payload into the internal table."""
    prices: dict[str, ModelPrice] = {}
    if not isinstance(value, Mapping):
        return prices
    for name, raw_price in value.items():
        model = str(name or "").strip()
        if not model or not isinstance(raw_price, Mapping):
            continue
        price = ModelPrice.from_mapping(raw_price)
        if price is not None:
            prices[model] = price
    return prices


def fetch_model_prices(url: str, timeout_seconds: float = 8.0) -> dict[str, ModelPrice]:
    """Fetch and normalize a JSON model-pricing table from an arbitrary URL."""
    target = str(url or "").strip()
    if not target:
        raise ValueError("pricing URL is empty")
    request = Request(
        target,
        headers={
            "Accept": "application/json",
            "User-Agent": "codex-usage-hud",
        },
    )
    try:
        with urlopen(request, timeout=max(1.0, timeout_seconds)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to fetch pricing: {exc}") from exc
    prices = extract_model_prices(payload)
    if not prices:
        raise ValueError("pricing payload did not contain supported model prices")
    return prices


def extract_model_prices(payload: Any) -> dict[str, ModelPrice]:
    """Extract model prices from common top-level JSON shapes."""
    if not isinstance(payload, Mapping):
        return {}
    for key in ("model_prices", "prices", "pricing", "models"):
        extracted = _extract_price_collection(payload.get(key))
        if extracted:
            return extracted
    return _extract_price_collection(payload)


def normalize_time_text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) < 2:
        return default
    hour = _optional_int(parts[0])
    minute = _optional_int(parts[1])
    if hour is None or minute is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return f"{hour:02d}:{minute:02d}"


def time_parts(value: str) -> tuple[int, int]:
    text = normalize_time_text(value, DEFAULT_DAILY_RESET_TIME)
    hour, minute = text.split(":", 1)
    return int(hour), int(minute)


def normalize_weekday(value: Any, default: int = DEFAULT_WEEKLY_RESET_WEEKDAY) -> int:
    if isinstance(value, str):
        text = value.strip().lower()
        names = {
            "monday": 0,
            "mon": 0,
            "tuesday": 1,
            "tue": 1,
            "wednesday": 2,
            "wed": 2,
            "thursday": 3,
            "thu": 3,
            "friday": 4,
            "fri": 4,
            "saturday": 5,
            "sat": 5,
            "sunday": 6,
            "sun": 6,
        }
        if text in names:
            return names[text]
    weekday = _optional_int(value)
    if weekday is None or not (0 <= weekday <= 6):
        return default
    return weekday


def normalize_display_mode(value: Any) -> str:
    mode = str(value or DEFAULT_DISPLAY_MODE).strip().lower().replace("-", "_")
    if mode in {"inject", "injection", "renderer_hud"}:
        mode = "renderer"
    if mode in {"tkinter", "tk_hud"}:
        mode = "tk"
    return mode if mode in VALID_DISPLAY_MODES else DEFAULT_DISPLAY_MODE


def effective_display_mode(value: Any) -> str:
    """Collapse a configured display preference into the active HUD surface."""
    return "tk" if normalize_display_mode(value) == "tk" else "renderer"


def _extract_price_collection(value: Any) -> dict[str, ModelPrice]:
    prices: dict[str, ModelPrice] = {}
    if isinstance(value, Mapping):
        for name, raw in value.items():
            if not isinstance(raw, Mapping):
                continue
            price = ModelPrice.from_mapping(raw.get("pricing") or raw.get("prices") or raw)
            model = str(raw.get("id") or raw.get("model") or raw.get("name") or name).strip()
            if model and price is not None:
                prices[model] = price
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, Mapping):
                continue
            price = ModelPrice.from_mapping(
                item.get("pricing") or item.get("prices") or item
            )
            model = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
            if model and price is not None:
                prices[model] = price
    return prices


def _first_present(value: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None or value == "" else int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_BUDGET_THRESHOLDS",
    "DEFAULT_DAILY_BUDGET_USD",
    "DEFAULT_DAILY_RESET_TIME",
    "DEFAULT_DISPLAY_MODE",
    "DEFAULT_SUPPORT_URL",
    "DEFAULT_WEEKLY_BUDGET_USD",
    "DEFAULT_WEEKLY_RESET_TIME",
    "DEFAULT_WEEKLY_RESET_WEEKDAY",
    "HUD_SETTINGS_FILENAME",
    "ModelPrice",
    "USER_CONFIG_KEY",
    "UserConfig",
    "UserConfigStore",
    "default_model_prices",
    "default_settings_path",
    "effective_display_mode",
    "extract_model_prices",
    "fetch_model_prices",
    "normalize_display_mode",
    "normalize_model_prices",
    "parse_thresholds",
    "read_json_object",
    "time_parts",
    "write_json_object",
]
