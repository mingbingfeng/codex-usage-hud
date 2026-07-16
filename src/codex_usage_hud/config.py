"""User-facing configuration for codex-usage-hud."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterable, Mapping
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .core.calculator import MODEL_PRICES

HUD_SETTINGS_FILENAME = "hud_settings.json"
USER_CONFIG_KEY = "user"
RUNTIME_STATE_KEY = "runtime"
WARNING_DISMISSED_DATE_KEY = "warning_dismissed_date"
DEFAULT_DAILY_BUDGET_USD = 100.0
DEFAULT_WEEKLY_BUDGET_USD = 400.0
DEFAULT_BUDGET_THRESHOLDS = (0.5, 0.8, 0.9, 1.0)
DEFAULT_DAILY_RESET_TIME = "10:00"
DEFAULT_WEEKLY_RESET_WEEKDAY = 3
DEFAULT_WEEKLY_RESET_TIME = "10:00"
DEFAULT_DISPLAY_MODE = "renderer"
VALID_DISPLAY_MODES = {"renderer"}
DEFAULT_SUPPORT_URL = "https://github.com/mingbingfeng/codex-usage-hud"
DEFAULT_WORK_OVERLAY_MAX_ITEMS = 6
DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED = False
JSON_WRITE_REPLACE_RETRIES = 8
JSON_WRITE_REPLACE_DELAY_SECONDS = 0.01

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
    model: str = ""
    provider: str = ""
    base_url: str = ""

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
            model=_optional_str(
                value.get("model") or value.get("model_pattern") or value.get("pattern")
            )
            or "",
            provider=normalize_provider(value.get("provider")),
            base_url=normalize_base_url(
                value.get("base_url") or value.get("baseUrl") or value.get("api_base")
            ),
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "input": float(self.input),
            "cached_input": float(self.cached_input),
            "output": float(self.output),
            "reasoning": float(self.reasoning),
        }
        if self.model:
            payload["model"] = self.model
        if self.provider:
            payload["provider"] = self.provider
        if self.base_url:
            payload["base_url"] = self.base_url
        return payload


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
class ProviderSettings:
    """User-managed pricing and adjustment values for one billing provider."""

    model_prices: dict[str, ModelPrice] = field(default_factory=default_model_prices)
    pricing_url: str = ""
    weekly_adjustment_usd: float = 0.0

    @classmethod
    def from_dict(cls, value: Any) -> "ProviderSettings | None":
        if not isinstance(value, Mapping):
            return None
        prices = normalize_model_prices(value.get("model_prices"))
        return cls(
            model_prices=prices or default_model_prices(),
            pricing_url=_optional_str(value.get("pricing_url")) or "",
            weekly_adjustment_usd=max(0.0, _optional_float(value.get("weekly_adjustment_usd")) or 0.0),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "model_prices": {
                name: price.to_dict() for name, price in sorted(self.model_prices.items())
            },
            "pricing_url": self.pricing_url,
            "weekly_adjustment_usd": float(self.weekly_adjustment_usd),
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
    work_overlay_max_items: int = DEFAULT_WORK_OVERLAY_MAX_ITEMS
    model_prices: dict[str, ModelPrice] = field(default_factory=default_model_prices)
    pricing_url: str = ""
    budget_thresholds: list[float] = field(
        default_factory=lambda: list(DEFAULT_BUDGET_THRESHOLDS)
    )
    weekly_adjustment_usd: float = 0.0
    provider_settings: dict[str, ProviderSettings] = field(default_factory=dict)
    provider_scope_mode: str = "all"
    selected_providers: list[str] = field(default_factory=list)
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
        legacy_overlay_enabled = _optional_bool(value.get("work_overlay_enabled"))
        work_overlay_max_items = normalize_work_overlay_max_items(
            value.get("work_overlay_max_items"),
            defaults.work_overlay_max_items,
        )
        if legacy_overlay_enabled is False:
            work_overlay_max_items = 0
        provider_settings = normalize_provider_settings(value.get("provider_settings"))
        scope_mode = str(value.get("provider_scope_mode") or "all").strip().lower()
        if scope_mode not in {"all", "custom"}:
            scope_mode = "all"
        selected_providers = normalize_provider_names(value.get("selected_providers"))
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
            work_overlay_max_items=work_overlay_max_items,
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
            provider_settings=provider_settings,
            provider_scope_mode=scope_mode,
            selected_providers=selected_providers,
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
            "work_overlay_max_items": int(self.work_overlay_max_items),
            "pricing_url": self.pricing_url,
            "budget_thresholds": list(self.budget_thresholds),
            "weekly_adjustment_usd": float(self.weekly_adjustment_usd),
            "provider_settings": {
                provider: settings.to_dict()
                for provider, settings in sorted(self.provider_settings.items())
            },
            "provider_scope_mode": self.provider_scope_mode,
            "selected_providers": list(self.selected_providers),
            "support_url": self.support_url,
            "model_prices": {
                name: price.to_dict()
                for name, price in sorted(self.model_prices.items())
            },
        }

    def price_table(self) -> dict[str, dict[str, object]]:
        table = {name: price.to_dict() for name, price in self.model_prices.items()}
        for provider, settings in self.provider_settings.items():
            for name, price in settings.model_prices.items():
                table[f"{provider}/{name}"] = {
                    **price.to_dict(),
                    "model": price.model or name,
                    "provider": provider,
                }
        return table

    def provider_price_table(self, provider: str) -> dict[str, dict[str, object]]:
        """Return one provider's price table, preserving legacy global settings as fallback."""
        normalized_provider = normalize_provider(provider)
        settings = self.provider_settings.get(normalized_provider)
        prices = settings.model_prices if settings is not None else self.model_prices
        return {
            name: {**price.to_dict(), "provider": normalized_provider}
            for name, price in prices.items()
        }

    def effective_provider_scope(
        self,
        app_provider: str = "",
    ) -> frozenset[str] | None:
        """Return the selected provider set, or ``None`` for the all-provider mode."""
        if self.provider_scope_mode != "custom":
            return None
        selected = set(normalize_provider_names(self.selected_providers))
        required_provider = normalize_provider(app_provider)
        if required_provider and required_provider != "unknown":
            selected.add(required_provider)
        return frozenset(selected)

    def weekly_adjustment_for_scope(
        self,
        providers: Iterable[str] | None,
    ) -> float:
        """Return provider adjustments for the same scope used by usage aggregation."""
        if not self.provider_settings:
            return max(0.0, float(self.weekly_adjustment_usd))
        scope = None if providers is None else set(normalize_provider_names(providers))
        return round(
            sum(
                max(0.0, float(settings.weekly_adjustment_usd))
                for provider, settings in self.provider_settings.items()
                if scope is None or provider in scope
            ),
            6,
        )

    def migrate_legacy_provider_settings(
        self,
        providers: Iterable[str],
        *,
        app_provider: str = "",
    ) -> "UserConfig":
        """Materialize legacy global pricing once the available providers are known."""
        if self.provider_settings:
            return self
        targets = [
            provider
            for provider in normalize_provider_names(providers)
            if provider != "unknown"
        ]
        if not targets:
            return self
        scoped: dict[str, dict[str, ModelPrice]] = {provider: {} for provider in targets}
        for name, price in self.model_prices.items():
            explicit_provider = normalize_provider(price.provider)
            if explicit_provider:
                if explicit_provider in scoped:
                    scoped[explicit_provider][name] = replace(price, provider=explicit_provider)
                continue
            for provider in targets:
                scoped[provider][name] = replace(price, provider=provider)
        required_provider = normalize_provider(app_provider)
        settings = {
            provider: ProviderSettings(
                model_prices=prices or default_model_prices(),
                pricing_url=self.pricing_url,
                weekly_adjustment_usd=(
                    self.weekly_adjustment_usd if provider == required_provider else 0.0
                ),
            )
            for provider, prices in scoped.items()
        }
        return replace(self, provider_settings=settings)

    def with_price_updates(
        self,
        prices: Mapping[str, ModelPrice],
        *,
        pricing_url: str | None = None,
        provider: str | None = None,
    ) -> "UserConfig":
        normalized_provider = normalize_provider(provider)
        if normalized_provider:
            settings = self.provider_settings.get(normalized_provider, ProviderSettings())
            next_prices = dict(settings.model_prices)
            next_prices.update(prices)
            next_settings = dict(self.provider_settings)
            next_settings[normalized_provider] = ProviderSettings(
                model_prices=next_prices,
                pricing_url=settings.pricing_url if pricing_url is None else pricing_url,
                weekly_adjustment_usd=settings.weekly_adjustment_usd,
            )
            return replace(self, provider_settings=next_settings)
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
    last_error: OSError | None = None
    for attempt in range(JSON_WRITE_REPLACE_RETRIES):
        try:
            temp_path.replace(path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt + 1 >= JSON_WRITE_REPLACE_RETRIES:
                break
            time.sleep(JSON_WRITE_REPLACE_DELAY_SECONDS)
    if last_error is not None:
        raise last_error


def local_date_key(now: datetime | None = None) -> str:
    """Return the local calendar date used for per-day UI state."""
    current = (now or datetime.now()).astimezone()
    return current.date().isoformat()


def warning_dismissed_today(
    path: Path | str | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether the expanded warning banner is dismissed for today."""
    settings_path = Path(path) if path is not None else default_settings_path()
    raw = read_json_object(settings_path)
    runtime = raw.get(RUNTIME_STATE_KEY)
    if not isinstance(runtime, Mapping):
        return False
    return str(runtime.get(WARNING_DISMISSED_DATE_KEY) or "") == local_date_key(now)


def dismiss_warning_for_today(
    path: Path | str | None = None,
    *,
    now: datetime | None = None,
) -> None:
    """Persist that the expanded warning banner should stay hidden today."""
    settings_path = Path(path) if path is not None else default_settings_path()
    raw = read_json_object(settings_path)
    runtime = raw.get(RUNTIME_STATE_KEY)
    if not isinstance(runtime, dict):
        runtime = {}
    runtime[WARNING_DISMISSED_DATE_KEY] = local_date_key(now)
    raw[RUNTIME_STATE_KEY] = runtime
    write_json_object(settings_path, raw)


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


def normalize_provider(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text


def normalize_provider_names(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return sorted({provider for item in value if (provider := normalize_provider(item))})


def normalize_provider_settings(value: Any) -> dict[str, ProviderSettings]:
    if not isinstance(value, Mapping):
        return {}
    settings: dict[str, ProviderSettings] = {}
    for raw_provider, raw_settings in value.items():
        provider = normalize_provider(raw_provider)
        parsed = ProviderSettings.from_dict(raw_settings)
        if provider and parsed is not None:
            settings[provider] = parsed
    return settings


def normalize_base_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        return text.rstrip("/").lower()
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            "",
            "",
        )
    )


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
    legacy_renderer_aliases = {"auto", "inject", "injection", "renderer_hud"}
    legacy_qt_aliases = {"qt", "qt_hud", "pyside", "pyside6"}
    legacy_tk_aliases = {"tk", "tkinter", "tk_hud"}
    if mode in legacy_renderer_aliases | legacy_qt_aliases | legacy_tk_aliases:
        return "renderer"
    return mode if mode in VALID_DISPLAY_MODES else DEFAULT_DISPLAY_MODE


def effective_display_mode(value: Any) -> str:
    """Collapse a configured display preference into the active HUD surface."""
    del value
    return "renderer"


def normalize_work_overlay_max_items(
    value: Any,
    default: int = DEFAULT_WORK_OVERLAY_MAX_ITEMS,
    *,
    max_items: int | None = None,
) -> int:
    amount = _optional_int(value)
    if amount is None:
        amount = int(default)
    amount = max(0, int(amount))
    if max_items is None:
        return amount
    return min(amount, max(0, int(max_items)))


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


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
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
    "DEFAULT_WORK_OVERLAY_MAX_ITEMS",
    "HUD_SETTINGS_FILENAME",
    "ModelPrice",
    "ProviderSettings",
    "RUNTIME_STATE_KEY",
    "USER_CONFIG_KEY",
    "UserConfig",
    "UserConfigStore",
    "WARNING_DISMISSED_DATE_KEY",
    "default_model_prices",
    "default_settings_path",
    "dismiss_warning_for_today",
    "effective_display_mode",
    "extract_model_prices",
    "fetch_model_prices",
    "local_date_key",
    "normalize_display_mode",
    "normalize_model_prices",
    "normalize_provider_names",
    "normalize_provider_settings",
    "normalize_work_overlay_max_items",
    "parse_thresholds",
    "read_json_object",
    "time_parts",
    "warning_dismissed_today",
    "write_json_object",
]
