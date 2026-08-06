"""Immutable price-version models and JSON import/export helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

PRICING_SCHEMA_VERSION = 1
PRICING_UNIT = "USD_per_1M_tokens"
PRICE_FIELDS = ("input", "cached_input", "cache_write", "output", "reasoning")
VALID_CREATED_BY = frozenset({"user_import", "user_edit", "builtin_migration"})
VALID_SOURCES = frozenset({"manual", "import", "builtin"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc_datetime(value: Any, *, field_name: str) -> datetime:
    """Parse a timezone-aware timestamp and normalize it to UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise ValueError(f"{field_name} must be finite")
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        try:
            parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from exc
    else:
        raise ValueError(f"{field_name} must be a timezone-aware timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def datetime_to_json(value: datetime) -> str:
    normalized = parse_utc_datetime(value, field_name="timestamp")
    return normalized.isoformat().replace("+00:00", "Z")


def normalize_provider(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_base_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        return text.rstrip("/").lower()
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )


def _price_decimal(value: Any, *, field_name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field_name} must be a number")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if not amount.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if amount < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return amount


@dataclass(frozen=True)
class PriceVersion:
    """One immutable price table row for a matching scope and time boundary."""

    version_id: str
    input: Decimal
    cached_input: Decimal
    cache_write: Decimal
    output: Decimal
    reasoning: Decimal
    effective_at: datetime
    created_at: datetime
    created_by: str
    source: str
    model: str = ""
    model_pattern: str = ""
    provider: str = ""
    base_url: str = ""

    def __post_init__(self) -> None:
        version_id = str(self.version_id or "").strip()
        if not version_id:
            raise ValueError("version_id is required")
        model = str(self.model or "").strip()
        model_pattern = str(self.model_pattern or "").strip()
        if not model and not model_pattern:
            raise ValueError("model or model_pattern is required")
        created_by = str(self.created_by or "").strip()
        if created_by not in VALID_CREATED_BY:
            raise ValueError(f"unsupported created_by: {created_by}")
        source = str(self.source or "").strip()
        if source not in VALID_SOURCES:
            raise ValueError(f"unsupported source: {source}")
        object.__setattr__(self, "version_id", version_id)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "model_pattern", model_pattern)
        object.__setattr__(self, "provider", normalize_provider(self.provider))
        object.__setattr__(self, "base_url", normalize_base_url(self.base_url))
        object.__setattr__(
            self,
            "effective_at",
            parse_utc_datetime(self.effective_at, field_name="effective_at"),
        )
        object.__setattr__(
            self,
            "created_at",
            parse_utc_datetime(self.created_at, field_name="created_at"),
        )
        object.__setattr__(self, "created_by", created_by)
        object.__setattr__(self, "source", source)
        for field_name in PRICE_FIELDS:
            object.__setattr__(
                self,
                field_name,
                _price_decimal(getattr(self, field_name), field_name=field_name),
            )

    @property
    def match_pattern(self) -> str:
        return self.model_pattern or self.model

    @property
    def scope_key(self) -> tuple[str, str, str]:
        return (
            self.provider,
            self.base_url,
            self.match_pattern.strip().lower(),
        )

    @property
    def conflict_key(self) -> tuple[str, str, str, datetime]:
        return (*self.scope_key, self.effective_at)

    @property
    def prices(self) -> dict[str, float]:
        return {field_name: float(getattr(self, field_name)) for field_name in PRICE_FIELDS}

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        now: datetime | None = None,
        default_created_by: str = "user_import",
        default_source: str = "import",
        deterministic_id: bool = False,
    ) -> "PriceVersion":
        if not isinstance(value, Mapping):
            raise ValueError("price entry must be an object")
        current = parse_utc_datetime(now or utc_now(), field_name="now")
        if "input" not in value or "output" not in value:
            raise ValueError("price entry requires input and output")
        input_price = _price_decimal(value.get("input"), field_name="input")
        output_price = _price_decimal(value.get("output"), field_name="output")
        cached_input = _price_decimal(
            value.get("cached_input", input_price), field_name="cached_input"
        )
        cache_write = _price_decimal(
            value.get("cache_write", Decimal("0")), field_name="cache_write"
        )
        reasoning = _price_decimal(
            value.get("reasoning", output_price), field_name="reasoning"
        )
        effective_at = parse_utc_datetime(value.get("effective_at"), field_name="effective_at")
        created_at = parse_utc_datetime(
            value.get("created_at", current), field_name="created_at"
        )
        model = str(value.get("model") or "").strip()
        model_pattern = str(value.get("model_pattern") or "").strip()
        provider = normalize_provider(value.get("provider"))
        base_url = normalize_base_url(value.get("base_url"))
        version_id = str(value.get("version_id") or "").strip()
        if not version_id and deterministic_id:
            identity = json.dumps(
                {
                    "provider": provider,
                    "base_url": base_url,
                    "model": model,
                    "model_pattern": model_pattern,
                    "effective_at": datetime_to_json(effective_at),
                    "input": str(input_price),
                    "cached_input": str(cached_input),
                    "cache_write": str(cache_write),
                    "output": str(output_price),
                    "reasoning": str(reasoning),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            version_id = str(uuid5(NAMESPACE_URL, f"codex-usage-hud:{identity}"))
        return cls(
            version_id=version_id or str(uuid4()),
            model=model,
            model_pattern=model_pattern,
            provider=provider,
            base_url=base_url,
            input=input_price,
            cached_input=cached_input,
            cache_write=cache_write,
            output=output_price,
            reasoning=reasoning,
            effective_at=effective_at,
            created_at=created_at,
            created_by=str(value.get("created_by") or default_created_by),
            source=str(value.get("source") or default_source),
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "version_id": self.version_id,
            "provider": self.provider,
            "base_url": self.base_url,
            "input": float(self.input),
            "cached_input": float(self.cached_input),
            "cache_write": float(self.cache_write),
            "output": float(self.output),
            "reasoning": float(self.reasoning),
            "effective_at": datetime_to_json(self.effective_at),
            "created_at": datetime_to_json(self.created_at),
            "created_by": self.created_by,
            "source": self.source,
        }
        if self.model:
            payload["model"] = self.model
        if self.model_pattern:
            payload["model_pattern"] = self.model_pattern
        return payload


@dataclass(frozen=True)
class PriceAuditRecord:
    audit_id: str
    action: str
    version_id: str
    occurred_at: datetime
    created_by: str
    replaced_version_id: str = ""

    def __post_init__(self) -> None:
        if not str(self.audit_id or "").strip():
            raise ValueError("audit_id is required")
        if not str(self.version_id or "").strip():
            raise ValueError("version_id is required")
        if self.created_by not in VALID_CREATED_BY:
            raise ValueError(f"unsupported created_by: {self.created_by}")
        object.__setattr__(
            self,
            "occurred_at",
            parse_utc_datetime(self.occurred_at, field_name="occurred_at"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PriceAuditRecord":
        return cls(
            audit_id=str(value.get("audit_id") or ""),
            action=str(value.get("action") or ""),
            version_id=str(value.get("version_id") or ""),
            replaced_version_id=str(value.get("replaced_version_id") or ""),
            occurred_at=parse_utc_datetime(
                value.get("occurred_at"), field_name="occurred_at"
            ),
            created_by=str(value.get("created_by") or ""),
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "audit_id": self.audit_id,
            "action": self.action,
            "version_id": self.version_id,
            "occurred_at": datetime_to_json(self.occurred_at),
            "created_by": self.created_by,
        }
        if self.replaced_version_id:
            payload["replaced_version_id"] = self.replaced_version_id
        return payload


@dataclass(frozen=True)
class PricingConflict:
    provider: str
    base_url: str
    model_pattern: str
    effective_at: datetime
    existing_version_id: str
    incoming_version_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model_pattern": self.model_pattern,
            "effective_at": datetime_to_json(self.effective_at),
            "existing_version_id": self.existing_version_id,
            "incoming_version_id": self.incoming_version_id,
        }


@dataclass(frozen=True)
class PricingImportPreview:
    versions: tuple[PriceVersion, ...]
    conflicts: tuple[PricingConflict, ...]
    added_count: int
    updated_count: int
    skipped_count: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "prices": [version.to_dict() for version in self.versions],
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "added": self.added_count,
            "updated": self.updated_count,
            "skipped": self.skipped_count,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PricingApplyResult:
    versions: tuple[PriceVersion, ...]
    audit: tuple[PriceAuditRecord, ...]
    added_count: int
    updated_count: int
    skipped_count: int


class PricingConflictError(ValueError):
    pass


def normalize_price_versions(value: Any) -> tuple[PriceVersion, ...]:
    """Load persisted versions, skipping malformed entries without breaking old config."""
    if not isinstance(value, (list, tuple)):
        return ()
    by_conflict: dict[tuple[str, str, str, datetime], PriceVersion] = {}
    used_ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        try:
            version = PriceVersion.from_mapping(raw)
        except ValueError:
            continue
        if version.version_id in used_ids:
            continue
        used_ids.add(version.version_id)
        by_conflict[version.conflict_key] = version
    return _sorted_versions(by_conflict.values())


def normalize_price_audit(value: Any) -> tuple[PriceAuditRecord, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    records: list[PriceAuditRecord] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        try:
            records.append(PriceAuditRecord.from_mapping(raw))
        except ValueError:
            continue
    return tuple(records)


def pricing_export_payload(versions: Iterable[PriceVersion]) -> dict[str, object]:
    return {
        "schema_version": PRICING_SCHEMA_VERSION,
        "unit": PRICING_UNIT,
        "prices": [version.to_dict() for version in _sorted_versions(versions)],
    }


def empty_pricing_template() -> dict[str, object]:
    return {
        "schema_version": PRICING_SCHEMA_VERSION,
        "unit": PRICING_UNIT,
        "__description": {
            "model": "Exact model name; use model_pattern for wildcard matching.",
            "provider": "Billing provider boundary; empty means global fallback.",
            "base_url": "Optional normalized API base URL match.",
            "prices": "input, cached_input, cache_write, output, and reasoning are USD per 1M tokens.",
            "effective_at": "ISO-8601 timestamp with timezone; usage before it keeps the older price.",
        },
        "prices": [],
    }


def minimal_price_example() -> dict[str, object]:
    return {
        "model": "your-model",
        "provider": "your-provider",
        "base_url": "https://api.example.com/v1",
        "input": 1.0,
        "cached_input": 0.1,
        "cache_write": 1.25,
        "output": 6.0,
        "reasoning": 6.0,
        "effective_at": "2026-01-01T00:00:00Z",
    }


def _decode_payload(payload: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("pricing JSON must be UTF-8") from exc
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload, parse_constant=lambda value: (_raise_json_constant(value)))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid pricing JSON: {exc.msg}") from exc
    else:
        decoded = payload
    if not isinstance(decoded, Mapping):
        raise ValueError("pricing JSON must be an object")
    return decoded


def _raise_json_constant(value: str) -> None:
    raise ValueError(f"invalid numeric constant: {value}")


def parse_pricing_import(
    payload: str | bytes | Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[tuple[PriceVersion, ...], tuple[str, ...]]:
    """Strictly parse a schema-v1 import without mutating existing state."""
    raw = _decode_payload(payload)
    if raw.get("schema_version") != PRICING_SCHEMA_VERSION or isinstance(
        raw.get("schema_version"), bool
    ):
        raise ValueError(f"schema_version must be {PRICING_SCHEMA_VERSION}")
    if raw.get("unit") != PRICING_UNIT:
        raise ValueError(f"unit must be {PRICING_UNIT}")
    raw_prices = raw.get("prices")
    if not isinstance(raw_prices, list):
        raise ValueError("prices must be an array")
    current = parse_utc_datetime(now or utc_now(), field_name="now")
    warnings: list[str] = []
    unknown_top = sorted(set(raw) - {"schema_version", "unit", "prices", "__description"})
    if unknown_top:
        warnings.append("ignored top-level fields: " + ", ".join(unknown_top))
    allowed_entry = {
        "version_id",
        "model",
        "model_pattern",
        "provider",
        "base_url",
        *PRICE_FIELDS,
        "effective_at",
        "created_at",
        "created_by",
        "source",
    }
    versions: list[PriceVersion] = []
    used_ids: set[str] = set()
    for index, raw_price in enumerate(raw_prices):
        if not isinstance(raw_price, Mapping):
            raise ValueError(f"prices[{index}] must be an object")
        unknown_entry = sorted(set(raw_price) - allowed_entry)
        if unknown_entry:
            warnings.append(
                f"prices[{index}] ignored fields: " + ", ".join(unknown_entry)
            )
        try:
            version = PriceVersion.from_mapping(raw_price, now=current)
        except ValueError as exc:
            raise ValueError(f"prices[{index}]: {exc}") from exc
        if version.effective_at > current:
            raise ValueError(f"prices[{index}]: effective_at must not be in the future")
        if version.version_id in used_ids:
            raise ValueError(f"prices[{index}]: duplicate version_id {version.version_id}")
        used_ids.add(version.version_id)
        versions.append(version)
    return tuple(versions), tuple(warnings)


def preview_price_versions(
    existing_versions: Iterable[PriceVersion],
    incoming_versions: Iterable[PriceVersion],
    *,
    warnings: Iterable[str] = (),
) -> PricingImportPreview:
    existing_by_key = {version.conflict_key: version for version in existing_versions}
    incoming_by_key: dict[tuple[str, str, str, datetime], PriceVersion] = {}
    conflicts: list[PricingConflict] = []
    skipped = 0
    for version in incoming_versions:
        previous = incoming_by_key.get(version.conflict_key)
        if previous is not None:
            skipped += 1
            conflicts.append(_pricing_conflict(previous, version))
        incoming_by_key[version.conflict_key] = version

    added = 0
    updated = 0
    for version in incoming_by_key.values():
        existing = existing_by_key.get(version.conflict_key)
        if existing is None:
            added += 1
        elif existing == version:
            skipped += 1
        else:
            updated += 1
            conflicts.append(_pricing_conflict(existing, version))
    return PricingImportPreview(
        versions=tuple(incoming_by_key.values()),
        conflicts=tuple(conflicts),
        added_count=added,
        updated_count=updated,
        skipped_count=skipped,
        warnings=tuple(warnings),
    )


def preview_pricing_import(
    existing_versions: Iterable[PriceVersion],
    payload: str | bytes | Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> PricingImportPreview:
    versions, warnings = parse_pricing_import(payload, now=now)
    return preview_price_versions(existing_versions, versions, warnings=warnings)


def apply_pricing_import(
    existing_versions: Iterable[PriceVersion],
    preview: PricingImportPreview,
    *,
    conflict_policy: str = "cancel",
    applied_at: datetime | None = None,
) -> PricingApplyResult:
    """Atomically build a replacement tuple; callers persist only this result."""
    policy = str(conflict_policy or "cancel").strip().lower()
    if policy == "replace":
        policy = "overwrite"
    if policy not in {"cancel", "overwrite"}:
        raise ValueError("conflict_policy must be cancel or overwrite")
    if preview.conflicts and policy == "cancel":
        raise PricingConflictError("pricing import has unresolved conflicts")

    current = parse_utc_datetime(applied_at or utc_now(), field_name="applied_at")
    merged = {version.conflict_key: version for version in existing_versions}
    audit: list[PriceAuditRecord] = []
    added = 0
    updated = 0
    skipped = 0
    for incoming in preview.versions:
        existing = merged.get(incoming.conflict_key)
        if existing == incoming:
            skipped += 1
            continue
        if existing is None:
            added += 1
            action = "add"
            replaced = ""
        else:
            updated += 1
            action = "replace"
            replaced = existing.version_id
        merged[incoming.conflict_key] = incoming
        audit.append(
            PriceAuditRecord(
                audit_id=str(uuid4()),
                action=action,
                version_id=incoming.version_id,
                replaced_version_id=replaced,
                occurred_at=current,
                created_by=incoming.created_by,
            )
        )
    return PricingApplyResult(
        versions=_sorted_versions(merged.values()),
        audit=tuple(audit),
        added_count=added,
        updated_count=updated,
        skipped_count=skipped,
    )


def _pricing_conflict(existing: PriceVersion, incoming: PriceVersion) -> PricingConflict:
    return PricingConflict(
        provider=incoming.provider,
        base_url=incoming.base_url,
        model_pattern=incoming.match_pattern,
        effective_at=incoming.effective_at,
        existing_version_id=existing.version_id,
        incoming_version_id=incoming.version_id,
    )


def _sorted_versions(versions: Iterable[PriceVersion]) -> tuple[PriceVersion, ...]:
    return tuple(
        sorted(
            versions,
            key=lambda version: (
                version.provider,
                version.base_url,
                version.match_pattern.lower(),
                version.effective_at,
                version.version_id,
            ),
        )
    )


__all__ = [
    "PRICE_FIELDS",
    "PRICING_SCHEMA_VERSION",
    "PRICING_UNIT",
    "PriceAuditRecord",
    "PriceVersion",
    "PricingApplyResult",
    "PricingConflict",
    "PricingConflictError",
    "PricingImportPreview",
    "apply_pricing_import",
    "datetime_to_json",
    "empty_pricing_template",
    "minimal_price_example",
    "normalize_price_audit",
    "normalize_price_versions",
    "parse_pricing_import",
    "parse_utc_datetime",
    "preview_price_versions",
    "preview_pricing_import",
    "pricing_export_payload",
    "utc_now",
]
