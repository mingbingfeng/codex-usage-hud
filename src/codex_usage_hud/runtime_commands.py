"""Renderer runtime command handlers with explicitly supplied services."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
from threading import Event
from types import SimpleNamespace
from typing import Any
import uuid

from . import runtime_settings
from . import __version__
from .config import (
    DEFAULT_WORK_OVERLAY_MAX_ITEMS,
    ModelPrice,
    ProviderSettings,
    UserConfig,
    dismiss_warning_for_today,
    extract_model_prices,
    fetch_model_prices,
)
from .core.calculator import UsageCalculator
from .core.parser import CostEstimator
from .desktop_overlay import DesktopWorkOverlay
from .desktop_overlay_setup import (
    _desktop_overlay_dependency_status,
    _pyside6_version,
    _set_force_desktop_overlay_missing,
    _start_desktop_overlay_install,
)
from .overlay_runtime import _handle_work_overlay_command
from .overlay_projection import _work_overlay_screen_max_items
from .platforms import SessionSwitchController
from .runtime_context import RuntimeContext, _build_usage_summary_cache
from .runtime_snapshot_service import _apply_pre_send_pricing
from .runtime_policies import budget_warning_messages
from .updater import AutoUpdateManager, check_for_update, download_update_asset, launch_installer


def _renderer_settings_status(
    message: str,
    *,
    kind: str = "info",
) -> dict[str, object]:
    return runtime_settings.settings_status(message, kind=kind)


def refresh_latest_snapshot_for_partial_settings_command(
    command: Mapping[str, Any],
    *,
    snapshot: object,
    context: object,
    previous_config: UserConfig,
    current_config: UserConfig,
) -> None:
    """Update only fields whose settings-domain payload is being pushed."""
    action = str(command.get("action") or "").strip()
    changed_keys = runtime_settings.changed_config_keys(
        previous_config, current_config
    )
    if action in {"fetchPrices", "savePricing", "pricingImportCommit"} or (
        action == "save"
        and changed_keys
        and changed_keys.issubset(runtime_settings.PRICING_KEYS)
    ):
        snapshot.estimate_base = _apply_pre_send_pricing(
            context, snapshot, snapshot.estimate_base
        )
    if action == "save" and changed_keys & runtime_settings.BUDGET_KEYS:
        raw_week_cost_usd = (
            max(
                0.0,
                float(snapshot.week_cost_usd)
                - float(snapshot.week_adjustment_usd or 0.0),
            )
            if snapshot.week_cost_usd is not None
            else None
        )
        week_adjustment_usd = max(
            0.0, float(current_config.weekly_adjustment_usd)
        )
        snapshot.week_adjustment_usd = week_adjustment_usd
        snapshot.week_cost_usd = (
            None
            if raw_week_cost_usd is None
            else round(raw_week_cost_usd + week_adjustment_usd, 6)
        )
        snapshot.daily_limit_usd = max(0.0, float(current_config.daily_budget_usd))
        snapshot.weekly_limit_usd = max(0.0, float(current_config.weekly_budget_usd))
        snapshot.budget_warnings = (
            budget_warning_messages(
                snapshot.today_cost_usd,
                snapshot.week_cost_usd,
                snapshot.daily_limit_usd,
                snapshot.weekly_limit_usd,
                list(current_config.budget_thresholds),
            )
            if snapshot.today_cost_usd is not None and snapshot.week_cost_usd is not None
            else []
        )

_LOGGER = logging.getLogger(__name__)
UNHANDLED = object()


@dataclass(frozen=True, slots=True)
class RuntimeCommandPorts:
    background_usage: object | None = None
    cleanup_worker: object | None = None
    insights_worker: object | None = None
    insights_payload: Mapping[str, object] | None = None
    activate_session: Callable[[Mapping[str, object]], object | None] | None = None


@dataclass(frozen=True, slots=True)
class GeneralCommandPorts:
    load_config: Callable[[], Any]
    save_config: Callable[[Any], None]
    fetch_prices: Callable[[str], Mapping[str, Any]]
    rest_reminder: object | None
    update_manager: object | None
    work_overlay: object | None
    request_restart: Callable[[], None]
    request_exit: Callable[[], None]
    check_update: Callable[[], object]
    install_update: Callable[[object], None]
    overlay_status: Callable[[], Mapping[str, object]]
    start_overlay_install: Callable[[], bool]
    clear_forced_missing: Callable[[], None]
    forced_missing_with_real_install: Callable[[], bool]
    pyside_version: Callable[[], str]
    default_overlay_limit: Callable[[], int]
    dismiss_warnings_today: Callable[[], bool]
    pricing_recalculation_preview: (
        Callable[[Mapping[str, object]], Mapping[str, object]] | None
    ) = None
    pricing_recalculation_execute: (
        Callable[[Mapping[str, object]], Mapping[str, object]] | None
    ) = None
    pricing_impact_preview: (
        Callable[[Mapping[str, object]], Mapping[str, object]] | None
    ) = None


def _status(message: str, *, kind: str = "") -> dict[str, object]:
    return runtime_settings.settings_status(message, kind=kind)


def _price_updates(
    previous: UserConfig, current: UserConfig
) -> list[tuple[str, dict[str, ModelPrice]]]:
    updates: list[tuple[str, dict[str, ModelPrice]]] = []
    global_updates = {
        key: price
        for key, price in current.model_prices.items()
        if previous.model_prices.get(key) != price
    }
    if global_updates:
        updates.append(("", global_updates))
    for provider in sorted(
        previous.provider_settings.keys() | current.provider_settings.keys()
    ):
        previous_prices = previous.provider_settings.get(
            provider, ProviderSettings()
        ).model_prices
        current_prices = current.provider_settings.get(
            provider, ProviderSettings()
        ).model_prices
        changed = {
            key: price
            for key, price in current_prices.items()
            if previous_prices.get(key) != price
        }
        if changed:
            updates.append((provider, changed))
    return updates


def _pricing_version_state_changed(previous: UserConfig, current: UserConfig) -> bool:
    """Reject direct version/audit writes outside the pricing workflows."""
    return (
        previous.pricing_versions != current.pricing_versions
        or previous.pricing_audit != current.pricing_audit
    )


def _merge_versioned_prices(
    candidate: UserConfig,
    versioned: UserConfig,
) -> UserConfig:
    provider_settings: dict[str, ProviderSettings] = {}
    for provider in sorted(
        candidate.provider_settings.keys() | versioned.provider_settings.keys()
    ):
        candidate_settings = candidate.provider_settings.get(provider, ProviderSettings())
        versioned_settings = versioned.provider_settings.get(provider, ProviderSettings())
        provider_settings[provider] = replace(
            candidate_settings,
            model_prices=versioned_settings.model_prices,
        )
    return replace(
        candidate,
        model_prices=versioned.model_prices,
        provider_settings=provider_settings,
        pricing_versions=versioned.pricing_versions,
        pricing_audit=versioned.pricing_audit,
    )


def _prepare_versioned_price_changes(
    ports: GeneralCommandPorts,
    settings_payload: object,
    effective_at: object,
) -> tuple[UserConfig, UserConfig, int]:
    """Build a versioned candidate without writing it.

    The settings dialog uses the same preparation as ``savePricing`` for its
    read-only impact preview.  Keeping this operation pure also prevents a
    preview request from accidentally publishing a partial price table.
    """
    previous = ports.load_config()
    candidate = runtime_settings.config_from_payload(previous, settings_payload)
    if _pricing_version_state_changed(previous, candidate):
        raise ValueError("价格版本只能通过保存价格或导入预览流程写入。")
    updates = _price_updates(previous, candidate)
    if not updates:
        return previous, candidate, 0
    if not str(effective_at or "").strip():
        raise ValueError("保存价格前必须设置新价格的生效时间。")
    versioned = previous
    changed_count = 0
    for provider, prices in updates:
        versioned, result = versioned.apply_price_updates(
            prices,
            effective_at=str(effective_at),
            provider=provider,
        )
        changed_count += int(result.added_count) + int(result.updated_count)
    final = _merge_versioned_prices(candidate, versioned)
    return previous, final, changed_count


def _save_versioned_price_changes(
    ports: GeneralCommandPorts,
    settings_payload: object,
    effective_at: object,
) -> tuple[UserConfig, int]:
    _previous, final, changed_count = _prepare_versioned_price_changes(
        ports,
        settings_payload,
        effective_at,
    )
    ports.save_config(final)
    return final, changed_count


def _pricing_payload_with_default_effective_at(
    payload: object,
    effective_at: object,
) -> dict[str, object]:
    default_effective_at = str(effective_at or "").strip()
    if isinstance(payload, Mapping) and isinstance(payload.get("prices"), list):
        normalized = dict(payload)
        normalized["schema_version"] = normalized.get("schema_version", 1)
        normalized["unit"] = normalized.get("unit", "USD_per_1M_tokens")
        if any(not isinstance(row, Mapping) for row in payload["prices"]):
            raise ValueError("prices entries must be objects")
        missing_effective_at = any(
            isinstance(row, Mapping) and not str(row.get("effective_at") or "").strip()
            for row in payload["prices"]
        )
        if missing_effective_at and not default_effective_at:
            raise ValueError("导入价格前必须设置新价格的生效时间。")
        normalized["prices"] = [
            {
                **dict(row),
                "effective_at": row.get("effective_at") or default_effective_at,
            }
            for row in payload["prices"]
        ]
        return normalized
    extracted = extract_model_prices(payload)
    if not extracted:
        raise ValueError("价格 JSON 中没有可导入的模型价格。")
    if not default_effective_at:
        raise ValueError("导入价格前必须设置新价格的生效时间。")
    return {
        "schema_version": 1,
        "unit": "USD_per_1M_tokens",
        "prices": [
            {
                **price.to_dict(),
                "model": price.model or key,
                "effective_at": default_effective_at,
                "created_by": "user_import",
                "source": "import",
            }
            for key, price in sorted(extracted.items())
        ],
    }


def _sync_imported_current_prices(
    config: UserConfig,
    preview: object,
    *,
    now: datetime | None = None,
) -> UserConfig:
    """Keep the editable current table aligned with the newest imported scope.

    A portable import may deliberately contain older historical versions.  The
    legacy ``model_prices`` table is still rendered as the editable current
    price, so it must be derived from the newest effective version after the
    merge rather than blindly from the last imported row.
    """
    current = now or datetime.now(timezone.utc)

    def scope_key(version: object) -> tuple[str, str, str]:
        return (
            str(getattr(version, "provider", "") or "").strip().lower(),
            str(getattr(version, "base_url", "") or "").strip().lower(),
            str(
                getattr(version, "model", "")
                or getattr(version, "model_pattern", "")
                or ""
            )
            .strip()
            .lower(),
        )

    touched_scopes = {
        scope_key(version)
        for version in getattr(preview, "versions", ())
        if scope_key(version)[2]
    }
    latest_by_scope: dict[tuple[str, str, str], object] = {}
    for version in getattr(config, "pricing_versions", ()):
        key = scope_key(version)
        effective_at = getattr(version, "effective_at", None)
        if key not in touched_scopes or not isinstance(effective_at, datetime):
            continue
        if effective_at > current:
            continue
        previous = latest_by_scope.get(key)
        if previous is None or (
            effective_at,
            getattr(version, "created_at", datetime.min.replace(tzinfo=timezone.utc)),
            str(getattr(version, "version_id", "")),
        ) > (
            getattr(previous, "effective_at", datetime.min.replace(tzinfo=timezone.utc)),
            getattr(previous, "created_at", datetime.min.replace(tzinfo=timezone.utc)),
            str(getattr(previous, "version_id", "")),
        ):
            latest_by_scope[key] = version

    result = config
    versions = sorted(
        latest_by_scope.values(),
        key=lambda version: (
            getattr(version, "effective_at", datetime.min.replace(tzinfo=timezone.utc)),
            getattr(version, "created_at", datetime.min.replace(tzinfo=timezone.utc)),
            str(getattr(version, "version_id", "")),
        ),
    )
    for version in versions:
        key = str(getattr(version, "model", "") or getattr(version, "model_pattern", ""))
        price = ModelPrice.from_mapping(version.to_dict(), key)
        if key and price is not None:
            result = result.with_price_updates(
                {key: price},
                provider=str(getattr(version, "provider", "") or "") or None,
            )
    return result


def _pricing_effective_at(command: Mapping[str, object]) -> str:
    return str(
        command.get("effectiveAt")
        or command.get("defaultEffectiveAt")
        or command.get("effective_at")
        or ""
    ).strip()


def _parse_pricing_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _empty_pricing_impact_bucket() -> dict[str, object]:
    return {
        "recordCount": 0,
        "pricedCount": 0,
        "unavailableCount": 0,
        "costUsd": 0.0,
        "previousCostUsd": 0.0,
        "nextCostUsd": 0.0,
    }


def _round_pricing_impact_bucket(bucket: dict[str, object]) -> dict[str, object]:
    for key in ("costUsd", "previousCostUsd", "nextCostUsd"):
        bucket[key] = round(float(bucket.get(key) or 0.0), 6)
    return bucket


def _pricing_impact_bucket_payload(
    *,
    before: dict[str, object],
    after: dict[str, object],
    effective_at: str,
) -> dict[str, object]:
    return {
        "effectiveAt": effective_at,
        "before": _round_pricing_impact_bucket(before),
        "after": _round_pricing_impact_bucket(after),
    }


def _candidate_config_for_pricing_command(
    ports: GeneralCommandPorts,
    command: Mapping[str, object],
) -> tuple[UserConfig, UserConfig, str]:
    """Return ``(previous, candidate, effective_at)`` without persistence."""
    previous = ports.load_config()
    effective_at = _pricing_effective_at(command)
    payload = command.get("payload")
    if payload is not None:
        normalized = _pricing_payload_with_default_effective_at(
            payload,
            effective_at,
        )
        preview = previous.preview_pricing_import(normalized)
        candidate, _result = previous.apply_pricing_import(
            preview,
            conflict_policy="overwrite",
        )
        return previous, _sync_imported_current_prices(candidate, preview), effective_at

    settings_payload = command.get("settings")
    if settings_payload is None and isinstance(command.get("candidate"), Mapping):
        settings_payload = command.get("candidate")
    if settings_payload is None and isinstance(command.get("prices"), Mapping):
        settings_payload = {"model_prices": dict(command["prices"])}
    candidate = runtime_settings.config_from_payload(previous, settings_payload)
    if _pricing_version_state_changed(previous, candidate):
        raise ValueError("价格版本只能通过保存价格或导入预览流程写入。")
    if _price_updates(previous, candidate):
        _previous, candidate, _changed_count = _prepare_versioned_price_changes(
            ports,
            settings_payload,
            effective_at,
        )
    return previous, candidate, effective_at


def _snapshot_pricing_impact(
    ledger: object,
    resolver: Callable[[object], tuple[float | None, str, Mapping[str, object] | None]],
    *,
    effective_at: str,
    provider: object = "",
    model: object = "",
    start_at: object = "",
    end_at: object = "",
) -> dict[str, object]:
    """Preview ordinary JSONL snapshots and split projected costs by time."""
    preview_method = getattr(ledger, "preview_recalculation", None)
    if not callable(preview_method):
        return {
            "matchedCount": 0,
            "changedCount": 0,
            "unavailableCount": 0,
            "previousTotalUsd": 0.0,
            "nextTotalUsd": 0.0,
            **_pricing_impact_bucket_payload(
                before=_empty_pricing_impact_bucket(),
                after=_empty_pricing_impact_bucket(),
                effective_at=effective_at,
            ),
        }
    try:
        raw = preview_method(
            resolver,
            provider=str(provider or "").strip().lower(),
            model=str(model or "").strip(),
            start_at=str(start_at or "").strip(),
            end_at=str(end_at or "").strip(),
            effective_at=effective_at,
        )
    except TypeError:
        raw = preview_method(
            resolver,
            provider=str(provider or "").strip().lower(),
            model=str(model or "").strip(),
            start_at=str(start_at or "").strip(),
            end_at=str(end_at or "").strip(),
        )
    payload = dict(raw.to_dict() if hasattr(raw, "to_dict") else raw)
    before = _empty_pricing_impact_bucket()
    after = _empty_pricing_impact_bucket()
    effective_datetime = _parse_pricing_datetime(effective_at)
    breakdown = payload.get("timeBreakdown")
    connector = getattr(ledger, "_connect", None)
    stored_decoder = getattr(ledger, "_stored", None)
    scope_where = getattr(ledger, "_scope_where", None)
    if isinstance(breakdown, Mapping):
        before.update(dict(breakdown.get("before") or {}))
        after.update(dict(breakdown.get("after") or {}))
    elif (
        effective_datetime is not None
        and callable(connector)
        and callable(stored_decoder)
        and callable(scope_where)
    ):
        where, values = scope_where(
            provider=str(provider or "").strip().lower(),
            model=str(model or "").strip(),
            start_at=str(start_at or "").strip(),
            end_at=str(end_at or "").strip(),
        )
        with closing(connector()) as connection:
            rows = connection.execute(
                "SELECT * FROM usage_price_snapshots" + where
                + " ORDER BY occurred_at, event_key",
                values,
            ).fetchall()
        for row in rows:
            stored = stored_decoder(row)
            occurred = _parse_pricing_datetime(getattr(stored, "occurred_at", ""))
            bucket = before if occurred is not None and occurred < effective_datetime else after
            old_cost = getattr(stored, "cost_usd", None)
            try:
                next_cost, _next_status, _next_snapshot = resolver(stored)
            except Exception:
                next_cost = None
            bucket["recordCount"] = int(bucket["recordCount"]) + 1
            if old_cost is not None:
                bucket["previousCostUsd"] = float(bucket["previousCostUsd"]) + float(old_cost)
            if next_cost is None:
                bucket["unavailableCount"] = int(bucket["unavailableCount"]) + 1
            else:
                bucket["pricedCount"] = int(bucket["pricedCount"]) + 1
                bucket["nextCostUsd"] = float(bucket["nextCostUsd"]) + float(next_cost)
                bucket["costUsd"] = float(bucket["costUsd"]) + float(next_cost)
    payload.update(
        _pricing_impact_bucket_payload(
            before=before,
            after=after,
            effective_at=effective_at,
        )
    )
    return payload


def _background_pricing_impact(
    runtime: object,
    calculator: UsageCalculator,
    *,
    effective_at: str,
    provider: object = "",
    model: object = "",
    start_at: object = "",
    end_at: object = "",
) -> dict[str, object]:
    """Read-only background request preview using the existing audit store."""
    scanner = getattr(runtime, "_scanner", None)
    store = getattr(scanner, "store", None) or getattr(runtime, "store", None)
    connect = getattr(store, "_connect", None)
    rows_for_scope = getattr(store, "_recalculation_rows", None)
    preview_rows = getattr(store, "_recalculation_preview", None)
    if callable(connect) and callable(rows_for_scope) and callable(preview_rows):
        with closing(connect()) as connection:
            scope, rows = rows_for_scope(
                connection,
                provider=provider,
                model=model,
                start_at=start_at,
                end_at=end_at,
            )
            raw, items = preview_rows(
                calculator,
                scope=scope,
                rows=rows,
            )
        before = _empty_pricing_impact_bucket()
        after = _empty_pricing_impact_bucket()
        effective_datetime = _parse_pricing_datetime(effective_at)
        items_by_id = {
            str(item.get("requestId") or ""): item
            for item in items
            if isinstance(item, Mapping)
        }
        for row in rows:
            occurred = datetime.fromtimestamp(
                int(row["occurred_at"] or 0), tz=timezone.utc
            )
            bucket = (
                before
                if effective_datetime is not None and occurred < effective_datetime
                else after
            )
            item = items_by_id.get(str(row["request_id"] or ""), {})
            old_cost = row["estimated_cost_usd"]
            next_cost = item.get("newCostUsd") if isinstance(item, Mapping) else None
            bucket["recordCount"] = int(bucket["recordCount"]) + 1
            if old_cost is not None:
                bucket["previousCostUsd"] = float(bucket["previousCostUsd"]) + float(old_cost)
            if next_cost is None:
                bucket["unavailableCount"] = int(bucket["unavailableCount"]) + 1
            else:
                bucket["pricedCount"] = int(bucket["pricedCount"]) + 1
                bucket["nextCostUsd"] = float(bucket["nextCostUsd"]) + float(next_cost)
                bucket["costUsd"] = float(bucket["costUsd"]) + float(next_cost)
        raw = dict(raw)
        raw.update(
            _pricing_impact_bucket_payload(
                before=before,
                after=after,
                effective_at=effective_at,
            )
        )
        return raw

    preview_method = getattr(runtime, "preview_recalculation", None)
    if not callable(preview_method):
        return {
            "requestCount": 0,
            "changedCount": 0,
            "beforeTotalUsd": 0.0,
            "afterTotalUsd": 0.0,
            "beforeUnavailableCount": 0,
            "afterUnavailableCount": 0,
            **_pricing_impact_bucket_payload(
                before=_empty_pricing_impact_bucket(),
                after=_empty_pricing_impact_bucket(),
                effective_at=effective_at,
            ),
        }
    try:
        raw = preview_method(
            provider=provider,
            model=model,
            start_at=start_at,
            end_at=end_at,
            effective_at=effective_at,
            calculator=calculator,
        )
    except TypeError:
        raw = preview_method(
            provider=provider,
            model=model,
            start_at=start_at,
            end_at=end_at,
        )
    payload = dict(raw if isinstance(raw, Mapping) else {})
    breakdown = payload.get("timeBreakdown")
    if isinstance(breakdown, Mapping):
        before = dict(breakdown.get("before") or {})
        after = dict(breakdown.get("after") or {})
    else:
        before = _empty_pricing_impact_bucket()
        after = _empty_pricing_impact_bucket()
    payload.update(
        _pricing_impact_bucket_payload(
            before=before,
            after=after,
            effective_at=effective_at,
        )
    )
    return payload


def correlate_status(
    status: dict[str, object], command: Mapping[str, object]
) -> dict[str, object]:
    status.setdefault("requestId", str(command.get("requestId") or command.get("id") or ""))
    status.setdefault("action", str(command.get("action") or ""))
    return status


def dispatch_command(
    command: Mapping[str, Any],
    runtime_ports: RuntimeCommandPorts,
    general_ports: GeneralCommandPorts,
) -> dict[str, object]:
    for handler in (
        handle_cleanup_command,
        handle_insights_command,
        handle_background_command,
    ):
        handled = handler(command, runtime_ports)
        if handled is not UNHANDLED:
            return correlate_status(handled, command)
    return correlate_status(handle_general_command(command, general_ports), command)


def _query_with_preview(
    runtime: object,
    *,
    range_key: str,
    feature: str,
    model: str,
    event_id: str,
) -> dict[str, object]:
    query = getattr(runtime, "query", None)
    if not callable(query):
        raise RuntimeError("用量总览当前不可用。")
    raw_payload = query(
        range_key=range_key,
        feature=feature,
        model=model,
        event_id=event_id,
    )
    if not isinstance(raw_payload, Mapping):
        raise RuntimeError("后台用量查询返回了无效数据。")
    payload = dict(raw_payload)
    selected_event_id = str(payload.get("selectedEventId") or "").strip()
    selected_detail: dict[str, object] | None = None
    detail = getattr(runtime, "detail", None)
    if selected_event_id and callable(detail):
        try:
            raw_detail = detail(selected_event_id)
        except Exception as exc:
            _LOGGER.debug(
                "background_usage_preview_failed event_id=%s error=%s",
                selected_event_id,
                exc,
            )
        else:
            if isinstance(raw_detail, Mapping):
                selected_detail = dict(raw_detail)
                prompt = str(selected_detail.pop("prompt", "") or "")
                selected_detail["hasPrompt"] = bool(prompt)
    payload["selectedDetail"] = selected_detail
    return payload


def handle_background_command(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object] | object:
    action = str(command.get("action") or "").strip()
    if action not in {
        "backgroundUsageQuery",
        "backgroundUsageDetail",
        "openBackgroundUsage",
        "openBackgroundUsageFromInsights",
    }:
        return UNHANDLED
    request_id = str(command.get("requestId") or command.get("id") or "").strip()
    runtime = ports.background_usage
    try:
        if action == "backgroundUsageQuery":
            raw_filters = command.get("filters")
            filters = raw_filters if isinstance(raw_filters, Mapping) else {}
            payload = _query_with_preview(
                runtime,
                range_key=str(filters.get("range") or "today"),
                feature=str(filters.get("feature") or ""),
                model=str(filters.get("model") or ""),
                event_id=str(filters.get("eventId") or ""),
            )
            return runtime_settings.background_usage_response_status(
                "query", request_id, payload=payload
            )
        event_id = str(command.get("eventId") or "").strip()
        if action == "backgroundUsageDetail":
            detail = getattr(runtime, "detail", None)
            if not callable(detail):
                return runtime_settings.background_usage_response_status(
                    "detail",
                    request_id,
                    event_id=event_id,
                    error="用量总览当前不可用。",
                )
            if command.get("markViewed") is True:
                confirm = getattr(runtime, "confirm", None)
                if callable(confirm):
                    confirm(event_id)
            payload = detail(event_id) if event_id else None
            return runtime_settings.background_usage_response_status(
                "detail",
                request_id,
                payload=payload,
                event_id=event_id,
                error="" if payload is not None else "后台用量事件不存在。",
            )
        if event_id:
            confirm = getattr(runtime, "confirm", None)
            if callable(confirm):
                confirm(event_id)
        range_key = "today"
        range_for_event = getattr(runtime, "range_for_event", None)
        if event_id and callable(range_for_event):
            candidate = str(range_for_event(event_id) or "today").strip().lower()
            if candidate in {"today", "7d", "30d", "all"}:
                range_key = candidate
        payload = _query_with_preview(
            runtime,
            range_key=range_key,
            feature="",
            model="",
            event_id=event_id,
        )
        return runtime_settings.background_usage_response_status(
            "open", request_id, payload=payload, event_id=event_id
        )
    except Exception as exc:
        kind = {
            "backgroundUsageQuery": "query",
            "backgroundUsageDetail": "detail",
        }.get(action, "open")
        return runtime_settings.background_usage_response_status(
            kind,
            request_id,
            event_id=str(command.get("eventId") or "").strip(),
            error=f"用量总览读取失败：{exc}",
        )


def handle_cleanup_command(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object] | object:
    action = str(command.get("action") or "").strip()
    if action not in runtime_settings.SESSION_CLEANUP_COMMANDS:
        return UNHANDLED
    request_id = str(command.get("requestId") or command.get("id") or "")
    enqueue = getattr(ports.cleanup_worker, "enqueue", None)
    if not callable(enqueue):
        status = _status("会话永久删除当前不可用。", kind="error")
        status["sessionCleanupRequestId"] = request_id
        status["sessionCleanupAction"] = action
        return status
    try:
        accepted = enqueue(command)
    except Exception as exc:
        status = _status(str(exc), kind="error")
        status["sessionCleanupRequestId"] = request_id
        status["sessionCleanupAction"] = action
        return status
    request_id = str(accepted.get("requestId") or request_id)
    labels = {
        "sessionCleanupScan": "会话清单扫描已开始。",
        "sessionCleanupPreview": "正在生成永久删除确认。",
        "sessionCleanupExecute": "永久删除请求已进入本地事务门禁。",
        "sessionCleanupCancel": "已取消会话删除确认。",
    }
    status = _status(labels.get(action, "会话清理命令已提交。"))
    status["sessionCleanupRequestId"] = request_id
    status["sessionCleanupAction"] = action
    return status


def actionable_session_ids(payload: Mapping[str, object] | None) -> set[str]:
    if not isinstance(payload, Mapping):
        return set()
    result: set[str] = set()
    for window_name in ("today", "week", "month"):
        window = payload.get(window_name)
        if not isinstance(window, Mapping):
            continue
        for collection_name in ("sessions", "topSessionsByUsage", "topSessionsByCost"):
            sessions = window.get(collection_name)
            if not isinstance(sessions, list):
                continue
            for item in sessions:
                if not isinstance(item, Mapping) or not bool(
                    item.get("actionable", item.get("canActivate", False))
                ):
                    continue
                session_id = str(item.get("id") or item.get("sessionId") or "").strip()
                try:
                    canonical = str(uuid.UUID(session_id))
                except (ValueError, AttributeError, TypeError):
                    continue
                if canonical == session_id.casefold():
                    result.add(canonical)
    return result


def handle_insights_command(
    command: Mapping[str, Any], ports: RuntimeCommandPorts
) -> dict[str, object] | object:
    action = str(command.get("action") or "").strip()
    if action not in {"usageInsightsRefresh", "openUsageInsightsSession"}:
        return UNHANDLED
    request_id = str(command.get("requestId") or command.get("id") or "")
    if action == "usageInsightsRefresh":
        refresh = getattr(ports.insights_worker, "request_refresh", None)
        if not callable(refresh) or not refresh(request_id=request_id):
            status = _status("用量洞察刷新器当前不可用。", kind="error")
        else:
            status = _status("用量洞察刷新已开始。")
        status["usageInsightsRequestId"] = request_id
        return status
    session_id = str(command.get("sessionId") or "").strip().casefold()
    if session_id not in actionable_session_ids(ports.insights_payload):
        return _status(
            "该会话已归档、标识不完整或不在当前洞察结果中，未执行跳转。",
            kind="error",
        )
    if ports.activate_session is None:
        return _status("当前 Renderer 会话切换器不可用。", kind="error")
    result = ports.activate_session(
        {
            "action": "activateSession",
            "sessionId": session_id,
            "targetTitle": str(command.get("targetTitle") or "").strip(),
            "workdir": str(command.get("workdir") or "").strip(),
        }
    )
    if result is None or not (
        bool(getattr(result, "ok", False))
        or str(getattr(result, "status", "")) == "already-active"
    ):
        return _status(
            str(getattr(result, "message", "") or "无法打开该会话。"), kind="error"
        )
    return _status("已切换到所选会话。")


def _update_status(state: object, fallback: str) -> dict[str, object]:
    return _status(
        str(getattr(state, "message", "") or getattr(state, "title", "") or fallback),
        kind="error" if getattr(state, "error", "") else "",
    )


def handle_general_command(
    command: Mapping[str, Any], ports: GeneralCommandPorts
) -> dict[str, object]:
    action = str(command.get("action") or "").strip()
    try:
        if action == "savePricing":
            _config, changed_count = _save_versioned_price_changes(
                ports,
                command.get("settings"),
                command.get("effectiveAt"),
            )
            return _status(f"已保存 {changed_count} 个新价格版本。")
        if action == "pricingImpactPreview":
            if ports.pricing_impact_preview is None:
                return _status("价格影响预览当前不可用。", kind="error")
            preview = dict(ports.pricing_impact_preview(command))
            status = _status("价格影响预览已生成，尚未修改任何记录。")
            status["pricingImpactPreview"] = preview
            status["pricingImpactEffectiveAt"] = _pricing_effective_at(command)
            return status
        if action in {"pricingImportPreview", "pricingImportCommit"}:
            payload = _pricing_payload_with_default_effective_at(
                command.get("payload"),
                command.get("defaultEffectiveAt"),
            )
            config = ports.load_config()
            preview = config.preview_pricing_import(payload)
            if action == "pricingImportPreview":
                status = _status(
                    "导入预览已生成；确认冲突处理后才会写入价格配置。"
                )
                preview_payload = preview.to_dict()
                preview_payload.update(
                    {
                        "addedCount": preview.added_count,
                        "updatedCount": preview.updated_count,
                        "skippedCount": preview.skipped_count,
                    }
                )
                status["pricingPreview"] = preview_payload
                status["pricingPayload"] = payload
                return status
            conflict_policy = str(command.get("conflictPolicy") or "cancel")
            updated, result = config.apply_pricing_import(
                preview,
                conflict_policy=conflict_policy,
            )
            updated = _sync_imported_current_prices(updated, preview)
            ports.save_config(updated)
            status = _status(
                "价格导入完成："
                f"新增 {result.added_count}，更新 {result.updated_count}，"
                f"跳过 {result.skipped_count}。"
            )
            status["pricingImportResult"] = {
                "addedCount": result.added_count,
                "updatedCount": result.updated_count,
                "skippedCount": result.skipped_count,
                "importedAt": datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
            }
            return status
        if action == "fetchPricesPreview":
            config = ports.load_config()
            provider = str(command.get("provider") or "").strip().lower()
            provider_url = (
                config.provider_settings[provider].pricing_url
                if provider and provider in config.provider_settings
                else config.pricing_url
            )
            url = str(command.get("url") or provider_url or "").strip()
            fetched = ports.fetch_prices(url)
            legacy_payload = {
                "model_prices": {
                    key: (
                        value.to_dict()
                        if isinstance(value, ModelPrice)
                        else dict(value)
                    )
                    for key, value in fetched.items()
                }
            }
            payload = _pricing_payload_with_default_effective_at(
                legacy_payload,
                command.get("defaultEffectiveAt"),
            )
            if provider:
                payload["prices"] = [
                    {**row, "provider": provider}
                    for row in payload["prices"]
                    if isinstance(row, Mapping)
                ]
            preview = config.preview_pricing_import(payload)
            status = _status(
                f"已拉取 {len(fetched)} 个模型价格；确认后才会保存。"
            )
            preview_payload = preview.to_dict()
            preview_payload.update(
                {
                    "addedCount": preview.added_count,
                    "updatedCount": preview.updated_count,
                    "skippedCount": preview.skipped_count,
                }
            )
            status["pricingPreview"] = preview_payload
            status["pricingPayload"] = payload
            status["pricingUrl"] = url
            return status
        if action == "pricingExport":
            now = datetime.now().astimezone()
            status = _status("当前价格 JSON 已生成。")
            status["pricingPayload"] = ports.load_config().export_pricing_payload()
            status["filename"] = f"codex-usage-hud-pricing-{now:%Y%m%d-%H%M}.json"
            status["mimeType"] = "application/json"
            return status
        if action == "pricingTemplate":
            status = _status("空价格模板已生成。")
            status["pricingPayload"] = UserConfig.empty_pricing_template()
            status["filename"] = "codex-usage-hud-pricing-template.json"
            status["mimeType"] = "application/json"
            return status
        if action == "pricingRecalculationPreview":
            if ports.pricing_recalculation_preview is None:
                return _status("历史费用重算当前不可用。", kind="error")
            preview = dict(ports.pricing_recalculation_preview(command))
            status = _status("历史费用差异预览已生成，尚未修改任何记录。")
            status["pricingRecalculationPreview"] = preview
            return status
        if action == "pricingRecalculationExecute":
            if ports.pricing_recalculation_execute is None:
                return _status("历史费用重算当前不可用。", kind="error")
            result = dict(ports.pricing_recalculation_execute(command))
            status = _status(
                f"历史费用重算完成，共更新 {int(result.get('changedCount') or 0)} 条记录。"
            )
            status["pricingRecalculationResult"] = result
            return status
        if action in {"save", "applyDisplayMode"}:
            settings_payload = command.get("settings")
            previous_config = ports.load_config()
            config = runtime_settings.config_from_payload(previous_config, settings_payload)
            if _pricing_version_state_changed(previous_config, config):
                raise ValueError("价格版本只能通过保存价格或导入预览流程写入。")
            if action == "save" and _price_updates(previous_config, config):
                raise ValueError("价格有变更，请先设置新价格的生效时间。")
            ports.save_config(config)
            if action == "applyDisplayMode":
                return _status(
                    "Renderer 方案已保存；当前会话已处于内嵌显示，无需重启。"
                )
            if str(command.get("section") or "") == "restReminder":
                started_at_ms = (
                    settings_payload.get("rest_reminder_timer_started_at_ms")
                    if isinstance(settings_payload, Mapping)
                    else None
                )
                if ports.rest_reminder is not None and started_at_ms is not None:
                    ports.rest_reminder.adjust_cycle_started_at_ms(started_at_ms)
                    status = _status("提醒设置已保存，已按指定时间校正本轮计时。")
                else:
                    status = _status("提醒设置已保存；休息结束后会自动开始下一轮。")
                status["restReminderSaved"] = True
                status["restReminderSaveRequestId"] = str(
                    command.get("requestId") or command.get("id") or ""
                )
                return status
            return _status("设置已保存，相关显示会自动刷新。")
        if action.startswith("restReminder"):
            reminder = ports.rest_reminder
            if action == "restReminderAck":
                if reminder is not None:
                    reminder.acknowledge()
                return _status("休息提醒状态已更新。")
            if action == "restReminderStart":
                ok = bool(reminder.start_rest()) if reminder is not None else False
                return _status(
                    "已开始休息计时。" if ok else "当前状态不能开始休息。",
                    kind="" if ok else "error",
                )
            if action == "restReminderFinish":
                ok = bool(reminder.finish_rest()) if reminder is not None else False
                return _status(
                    "本次休息已结束，新一轮专注计时已开始。"
                    if ok
                    else "当前没有正在进行的休息。",
                    kind="" if ok else "error",
                )
            if action == "restReminderPostpone":
                ok = bool(reminder.postpone()) if reminder is not None else False
                return _status(
                    "已安排稍后提醒。" if ok else "这次提醒已经延后过了。",
                    kind="" if ok else "error",
                )
            result = (
                reminder.test_notification()
                if reminder is not None
                else {"status": "failed", "error": "提醒服务未启动"}
            )
            sent = str(result.get("status") or "") == "sent"
            if bool(result.get("preview")):
                return _status(
                    "已发送系统通知，并弹出实际休息提醒预览。关闭预览不会改变当前计时。"
                    if sent
                    else f"已弹出实际休息提醒预览；系统通知失败：{result.get('error') or '未知错误'}",
                    kind="" if sent else "error",
                )
            return _status(
                "系统通知测试已发送。"
                if sent
                else f"系统通知发送失败：{result.get('error') or '未知错误'}",
                kind="" if sent else "error",
            )
        if action == "fetchPrices":
            return _status(
                "旧版直接拉取已停用，请先预览价格并设置新价格的生效时间。",
                kind="error",
            )
        if action == "restart":
            ports.request_restart()
            return _status("已请求重启 HUD；daemon 模式会自动恢复。")
        if action == "exit":
            ports.request_exit()
            return _status("已请求退出 HUD；后台守护进程也会一并停止。")
        if action == "checkUpdate":
            if ports.update_manager is not None:
                return _update_status(
                    ports.update_manager.request_check(auto_download=False),
                    "正在检查更新...",
                )
            info = ports.check_update()
            if getattr(info, "error", ""):
                return _status(f"检查更新失败：{info.error}", kind="error")
            if getattr(info, "available", False):
                return _status(
                    f"发现新版本 {info.latest_version}，安装包：{info.asset_name}"
                )
            return _status(f"当前已是最新版本（{info.current_version}）。")
        if action == "installUpdate":
            if ports.update_manager is not None:
                return _update_status(
                    ports.update_manager.request_install(), "正在准备安装更新..."
                )
            info = ports.check_update()
            if getattr(info, "error", ""):
                return _status(f"检查更新失败：{info.error}", kind="error")
            if not getattr(info, "available", False):
                return _status(f"当前已是最新版本（{info.current_version}）。")
            ports.install_update(info)
            ports.request_restart()
            return _status(f"已启动 {info.asset_name}，安装器会先关闭当前 HUD。")
        if action == "installDesktopOverlay":
            status = ports.overlay_status()
            version = str(status.get("version") or "").strip()
            if bool(status.get("installed")):
                return _status(
                    f"气泡组件已可用{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。"
                )
            if bool(status.get("installing")):
                return _status("气泡组件正在安装；完成后点击“启用气泡”。")
            if not bool(status.get("canInstall")):
                return runtime_settings.settings_status(
                    "当前运行环境不能在线安装气泡组件；请安装带会话进度气泡的版本后重启 HUD。",
                    kind="error",
                    restart_visible=bool(status.get("requiresRestart")),
                )
            if ports.forced_missing_with_real_install():
                ports.clear_forced_missing()
                version = ports.pyside_version()
                return _status(
                    f"已检测到本机已安装气泡组件{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。"
                )
            if ports.start_overlay_install():
                refreshed = ports.overlay_status()
                if bool(refreshed.get("installed")):
                    version = str(refreshed.get("version") or "").strip()
                    return _status(
                        f"已检测到本机已安装气泡组件{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。"
                    )
                return _status("已开始安装气泡组件；完成后点击“启用气泡”。")
            return _status(
                "无法启动 PySide6 安装；请在终端运行 pip install PySide6>=6.8。",
                kind="error",
            )
        if action == "enableDesktopOverlay":
            if ports.forced_missing_with_real_install():
                ports.clear_forced_missing()
            status = ports.overlay_status()
            if not bool(status.get("installed")):
                return runtime_settings.settings_status(
                    "还没检测到气泡组件；安装完成后再点一次“启用气泡”。",
                    kind="error",
                    restart_visible=bool(status.get("requiresRestart")),
                )
            config = ports.load_config()
            if int(config.work_overlay_max_items or 0) <= 0:
                config = replace(
                    config, work_overlay_max_items=ports.default_overlay_limit()
                )
                ports.save_config(config)
            if ports.work_overlay is not None:
                ports.work_overlay.reset_runtime_availability()
            version = str(status.get("version") or "").strip()
            return _status(
                f"会话进度气泡已启用{f'（PySide6 {version}）' if version else ''}。"
            )
        if action == "updateAction":
            if ports.update_manager is None:
                return _status("当前会话未启用自动更新控制器。", kind="error")
            return _update_status(
                ports.update_manager.handle_click(), "更新操作已提交。"
            )
        if action == "dismissWarningsToday":
            if not ports.dismiss_warnings_today():
                return _status("无法保存预警关闭状态：配置路径不可用。", kind="error")
            return _status("今天不再显示预算预警。")
        return _status(f"无法处理未知设置命令：{action or 'empty'}", kind="error")
    except Exception as exc:
        return _status(f"设置命令执行失败：{exc}", kind="error")



def _handle_renderer_session_cleanup_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
) -> dict[str, object]:
    result = handle_cleanup_command(
        command,
        RuntimeCommandPorts(
            cleanup_worker=getattr(context, "session_cleanup_worker", None)
        ),
    )
    if result is UNHANDLED:
        return _renderer_settings_status("无法处理未知会话清理命令。", kind="error")
    return result


def _usage_insights_actionable_session_ids(context: object) -> set[str]:
    return actionable_session_ids(
        getattr(context, "usage_insights_payload", {})
    )


def _handle_renderer_usage_insights_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
    *,
    session_controller: SessionSwitchController | None,
) -> dict[str, object]:
    ports = RuntimeCommandPorts(
        insights_worker=getattr(context, "usage_insights_worker", None),
        insights_payload=getattr(context, "usage_insights_payload", None),
        activate_session=(
            lambda activation: _handle_work_overlay_command(
                activation,
                session_controller,
                prepare_window=True,
                backend_names=("cdp",),
            )
            if session_controller is not None
            else None
        ),
    )
    result = handle_insights_command(command, ports)
    if result is UNHANDLED:
        return _renderer_settings_status("无法处理未知用量洞察命令。", kind="error")
    return result


def _handle_renderer_settings_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
    restart_requested: Event,
    exit_requested: Event,
    update_manager: AutoUpdateManager | None = None,
    work_overlay: DesktopWorkOverlay | None = None,
    session_controller: SessionSwitchController | None = None,
) -> dict[str, object]:
    command_ports = RuntimeCommandPorts(
        background_usage=getattr(context, "background_usage_runtime", None),
        cleanup_worker=getattr(context, "session_cleanup_worker", None),
        insights_worker=getattr(context, "usage_insights_worker", None),
        insights_payload=getattr(context, "usage_insights_payload", None),
        activate_session=(
            lambda activation: _handle_work_overlay_command(
                activation,
                session_controller,
                prepare_window=True,
                backend_names=("cdp",),
            )
            if session_controller is not None
            else None
        ),
    )
    settings_store = getattr(context, "settings_store", None)
    settings_path = getattr(settings_store, "path", None)

    def load_config() -> UserConfig:
        load = getattr(settings_store, "load", None)
        return load() if callable(load) else UserConfig.defaults()

    def save_config(config: UserConfig) -> None:
        if settings_store is None:
            raise RuntimeError("配置存储当前不可用。")
        settings_store.save(config)
        context.settings_mtime = None
        context.reload_user_config()

    def install_update(info: object) -> None:
        installer = download_update_asset(info)
        launch_installer(installer)

    def recalculation_scope(command_payload: Mapping[str, object]) -> dict[str, str]:
        return {
            "provider": str(command_payload.get("provider") or "").strip().lower(),
            "model": str(command_payload.get("model") or "").strip(),
            "start_at": str(
                command_payload.get("startAt") or command_payload.get("from") or ""
            ).strip(),
            "end_at": str(
                command_payload.get("endAt") or command_payload.get("to") or ""
            ).strip(),
        }

    def preview_recalculation(
        command_payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        scope = recalculation_scope(command_payload)
        estimator = getattr(getattr(context, "parser", None), "cost_estimator", None)
        ledger = getattr(estimator, "pricing_ledger", None)
        ordinary: dict[str, object] = {
            "matchedCount": 0,
            "changedCount": 0,
            "unavailableCount": 0,
            "previousTotalUsd": 0.0,
            "nextTotalUsd": 0.0,
        }
        if ledger is not None and callable(getattr(estimator, "recalculate_snapshot", None)):
            ordinary = ledger.preview_recalculation(
                estimator.recalculate_snapshot,
                **scope,
            ).to_dict()
        background: dict[str, object] = {
            "matchedCount": 0,
            "changedCount": 0,
            "unavailableCount": 0,
            "previousTotalUsd": 0.0,
            "nextTotalUsd": 0.0,
        }
        background_runtime = getattr(context, "background_usage_runtime", None)
        background_preview = getattr(background_runtime, "preview_recalculation", None)
        if callable(background_preview):
            raw_background = dict(background_preview(**scope))
            background = {
                **raw_background,
                "matchedCount": int(raw_background.get("requestCount") or 0),
                "unavailableCount": int(
                    raw_background.get("afterUnavailableCount") or 0
                ),
                "previousTotalUsd": float(
                    raw_background.get("beforeTotalUsd") or 0.0
                ),
                "nextTotalUsd": float(raw_background.get("afterTotalUsd") or 0.0),
            }
        context._pricing_recalculation_preview_scope = tuple(scope.items())
        return {
            **scope,
            "matchedCount": int(ordinary.get("matchedCount") or 0)
            + int(background.get("matchedCount") or 0),
            "changedCount": int(ordinary.get("changedCount") or 0)
            + int(background.get("changedCount") or 0),
            "unavailableCount": int(ordinary.get("unavailableCount") or 0)
            + int(background.get("unavailableCount") or 0),
            "previousTotalUsd": round(
                float(ordinary.get("previousTotalUsd") or 0.0)
                + float(background.get("previousTotalUsd") or 0.0),
                6,
            ),
            "nextTotalUsd": round(
                float(ordinary.get("nextTotalUsd") or 0.0)
                + float(background.get("nextTotalUsd") or 0.0),
                6,
            ),
            "components": {"sessions": ordinary, "background": background},
        }

    def preview_pricing_impact(
        command_payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        previous, candidate, effective_at = _candidate_config_for_pricing_command(
            SimpleNamespace(load_config=load_config),
            command_payload,
        )
        if not effective_at:
            raise ValueError("预览价格影响前必须设置新价格的生效时间。")
        del previous
        calculator = UsageCalculator(
            candidate.price_table(),
            pricing_versions=getattr(candidate, "pricing_versions", ()),
        )
        estimator = CostEstimator(calculator)
        scope = {
            "provider": str(command_payload.get("provider") or "").strip().lower(),
            "model": str(command_payload.get("model") or "").strip(),
            "start_at": str(
                command_payload.get("startAt") or command_payload.get("start_at") or ""
            ).strip(),
            "end_at": str(
                command_payload.get("endAt") or command_payload.get("end_at") or ""
            ).strip(),
        }
        parser = getattr(context, "parser", None)
        current_estimator = getattr(parser, "cost_estimator", None)
        ledger = getattr(current_estimator, "pricing_ledger", None)
        ordinary = _snapshot_pricing_impact(
            ledger,
            estimator.recalculate_snapshot,
            effective_at=effective_at,
            **scope,
        )
        background_runtime = getattr(context, "background_usage_runtime", None)
        background = _background_pricing_impact(
            background_runtime,
            calculator,
            effective_at=effective_at,
            **scope,
        ) if background_runtime is not None else _background_pricing_impact(
            SimpleNamespace(),
            calculator,
            effective_at=effective_at,
            **scope,
        )

        def combine(period: str) -> dict[str, object]:
            left = ordinary.get(period)
            right = background.get(period)
            left = left if isinstance(left, Mapping) else {}
            right = right if isinstance(right, Mapping) else {}
            return _round_pricing_impact_bucket(
                {
                    "recordCount": int(left.get("recordCount") or 0)
                    + int(right.get("recordCount") or 0),
                    "pricedCount": int(left.get("pricedCount") or 0)
                    + int(right.get("pricedCount") or 0),
                    "unavailableCount": int(left.get("unavailableCount") or 0)
                    + int(right.get("unavailableCount") or 0),
                    "costUsd": float(left.get("costUsd") or 0.0)
                    + float(right.get("costUsd") or 0.0),
                    "previousCostUsd": float(left.get("previousCostUsd") or 0.0)
                    + float(right.get("previousCostUsd") or 0.0),
                    "nextCostUsd": float(left.get("nextCostUsd") or 0.0)
                    + float(right.get("nextCostUsd") or 0.0),
                }
            )

        return {
            "effectiveAt": effective_at,
            "ordinary": ordinary,
            "sessions": ordinary,
            "background": background,
            "components": {"sessions": ordinary, "background": background},
            "before": combine("before"),
            "after": combine("after"),
        }

    def execute_recalculation(
        command_payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        scope = recalculation_scope(command_payload)
        if getattr(context, "_pricing_recalculation_preview_scope", None) != tuple(
            scope.items()
        ):
            raise ValueError("请先预览同一范围的历史费用差异。")
        estimator = getattr(getattr(context, "parser", None), "cost_estimator", None)
        ledger = getattr(estimator, "pricing_ledger", None)
        ordinary: dict[str, object] = {"matchedCount": 0, "changedCount": 0}
        if ledger is not None and callable(getattr(estimator, "recalculate_snapshot", None)):
            preview = ledger.preview_recalculation(
                estimator.recalculate_snapshot,
                **scope,
            )
            ordinary = dict(ledger.apply_recalculation(preview))
        background: dict[str, object] = {"matchedCount": 0, "changedCount": 0}
        background_runtime = getattr(context, "background_usage_runtime", None)
        background_execute = getattr(background_runtime, "execute_recalculation", None)
        if callable(background_execute):
            raw_background = dict(background_execute(**scope))
            background = {
                **raw_background,
                "matchedCount": int(raw_background.get("requestCount") or 0),
            }
        context._pricing_recalculation_preview_scope = None
        context.current_session_tail_state = None
        parser = getattr(context, "parser", None)
        if parser is not None:
            context.usage_cache = _build_usage_summary_cache(parser)
        publish = getattr(getattr(context, "runtime_events", None), "publish", None)
        if callable(publish):
            publish(
                "pricing_recalculated",
                source="settings",
                context={"scope": scope},
            )
        return {
            "matchedCount": int(ordinary.get("matchedCount") or 0)
            + int(background.get("matchedCount") or 0),
            "changedCount": int(ordinary.get("changedCount") or 0)
            + int(background.get("changedCount") or 0),
            "components": {"sessions": ordinary, "background": background},
        }

    general_ports = GeneralCommandPorts(
        load_config=load_config,
        save_config=save_config,
        fetch_prices=fetch_model_prices,
        rest_reminder=getattr(context, "rest_reminder", None),
        update_manager=update_manager,
        work_overlay=work_overlay,
        request_restart=restart_requested.set,
        request_exit=exit_requested.set,
        check_update=lambda: check_for_update(current_version=__version__),
        install_update=install_update,
        overlay_status=_desktop_overlay_dependency_status,
        start_overlay_install=_start_desktop_overlay_install,
        clear_forced_missing=lambda: _set_force_desktop_overlay_missing(False),
        forced_missing_with_real_install=lambda: bool(
            _desktop_overlay_dependency_status().get("forcedMissing")
            and _desktop_overlay_dependency_status().get("realInstalled")
        ),
        pyside_version=_pyside6_version,
        default_overlay_limit=lambda: min(
            DEFAULT_WORK_OVERLAY_MAX_ITEMS, _work_overlay_screen_max_items()
        ),
        dismiss_warnings_today=lambda: bool(
            settings_path is not None and not dismiss_warning_for_today(settings_path)
        ),
        pricing_recalculation_preview=preview_recalculation,
        pricing_recalculation_execute=execute_recalculation,
        pricing_impact_preview=preview_pricing_impact,
    )
    return dispatch_command(command, command_ports, general_ports)

__all__ = [
    "RuntimeCommandPorts",
    "GeneralCommandPorts",
    "UNHANDLED",
    "actionable_session_ids",
    "correlate_status",
    "dispatch_command",
    "handle_background_command",
    "handle_cleanup_command",
    "handle_insights_command",
    "handle_general_command",
]
