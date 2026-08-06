from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codex_usage_hud.config import UserConfig
from codex_usage_hud.core.pricing_snapshots import PricingSnapshotLedger
from codex_usage_hud.runtime_commands import (
    GeneralCommandPorts,
    RuntimeCommandPorts,
    _snapshot_pricing_impact,
    dispatch_command,
    handle_general_command,
)


def _ports(state: dict[str, UserConfig], **overrides: object) -> GeneralCommandPorts:
    values = {
        "load_config": lambda: state["config"],
        "save_config": lambda value: state.__setitem__("config", value),
        "fetch_prices": lambda _url: {},
        "rest_reminder": None,
        "update_manager": None,
        "work_overlay": None,
        "request_restart": lambda: None,
        "request_exit": lambda: None,
        "check_update": lambda: SimpleNamespace(
            error="", available=False, current_version="1"
        ),
        "install_update": lambda _info: None,
        "overlay_status": lambda: {},
        "start_overlay_install": lambda: False,
        "clear_forced_missing": lambda: None,
        "forced_missing_with_real_install": lambda: False,
        "pyside_version": lambda: "",
        "default_overlay_limit": lambda: 1,
        "dismiss_warnings_today": lambda: True,
    }
    values.update(overrides)
    return GeneralCommandPorts(**values)


def test_save_pricing_requires_effective_time_and_creates_version() -> None:
    state = {"config": UserConfig.defaults()}
    payload = state["config"].to_dict()
    payload["model_prices"]["gpt-5.6-sol"]["input"] = 7.0
    ports = _ports(state)

    rejected = handle_general_command(
        {"action": "save", "settings": payload}, ports
    )
    assert rejected["kind"] == "error"
    assert state["config"].model_prices["gpt-5.6-sol"].input == 5.0

    saved = handle_general_command(
        {
            "action": "savePricing",
            "settings": payload,
            "effectiveAt": "2026-08-01T00:00:00Z",
        },
        ports,
    )
    assert not saved["kind"]
    assert state["config"].model_prices["gpt-5.6-sol"].input == 7.0
    version = next(
        version
        for version in state["config"].pricing_versions
        if version.match_pattern == "gpt-5.6-sol"
    )
    assert version.input == 7
    assert version.effective_at.isoformat() == "2026-08-01T00:00:00+00:00"


def test_import_preview_is_read_only_and_commit_is_atomic() -> None:
    state = {"config": UserConfig.defaults()}
    ports = _ports(state)
    payload = {
        "schema_version": 1,
        "unit": "USD_per_1M_tokens",
        "prices": [
            {
                "model": "gpt-imported",
                "provider": "custom",
                "input": 1,
                "output": 2,
                "cached_input": 0.1,
                "cache_write": 1.25,
                "reasoning": 2,
                "effective_at": "2026-08-01T00:00:00Z",
            }
        ],
    }

    preview = handle_general_command(
        {"action": "pricingImportPreview", "payload": payload}, ports
    )
    assert preview["pricingPreview"]["addedCount"] == 1
    assert state["config"].pricing_versions == ()

    committed = handle_general_command(
        {
            "action": "pricingImportCommit",
            "payload": preview["pricingPayload"],
            "conflictPolicy": "overwrite",
        },
        ports,
    )
    assert committed["pricingImportResult"]["addedCount"] == 1
    assert len(state["config"].pricing_versions) == 1
    assert state["config"].provider_settings["custom"].model_prices[
        "gpt-imported"
    ].cache_write == 1.25

    before = state["config"].to_dict()
    invalid = handle_general_command(
        {
            "action": "pricingImportCommit",
            "payload": {
                "schema_version": 1,
                "unit": "USD_per_1M_tokens",
                "prices": [{"model": "broken", "input": -1, "output": 2}],
            },
            "defaultEffectiveAt": "2026-08-01T00:00:00Z",
            "conflictPolicy": "overwrite",
        },
        ports,
    )
    assert invalid["kind"] == "error"
    assert state["config"].to_dict() == before

    missing_effective = {
        **payload,
        "prices": [{key: value for key, value in payload["prices"][0].items() if key != "effective_at"}],
    }
    rejected_missing_time = handle_general_command(
        {"action": "pricingImportPreview", "payload": missing_effective}, ports
    )
    assert rejected_missing_time["kind"] == "error"
    assert state["config"].to_dict() == before

    mixed_rows = {
        **payload,
        "prices": [payload["prices"][0], "not-a-price-object"],
    }
    rejected_mixed_rows = handle_general_command(
        {"action": "pricingImportPreview", "payload": mixed_rows}, ports
    )
    assert rejected_mixed_rows["kind"] == "error"
    assert state["config"].to_dict() == before


def test_import_of_an_older_version_keeps_the_editable_current_price() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    config, _ = UserConfig.defaults().apply_price_updates(
        {"gpt-test": {"input": 1, "output": 2}},
        effective_at=now - timedelta(days=4),
        provider="custom",
        created_at=now,
    )
    config, _ = config.apply_price_updates(
        {"gpt-test": {"input": 3, "output": 4}},
        effective_at=now - timedelta(days=1),
        provider="custom",
        created_at=now,
    )
    state = {"config": config}
    ports = _ports(state)
    historical_payload = {
        "schema_version": 1,
        "unit": "USD_per_1M_tokens",
        "prices": [
            {
                "model": "gpt-test",
                "provider": "custom",
                "input": 0.5,
                "cached_input": 0.05,
                "cache_write": 0.75,
                "output": 1,
                "reasoning": 1,
                "effective_at": (now - timedelta(days=6)).isoformat(),
            }
        ],
    }

    preview = handle_general_command(
        {"action": "pricingImportPreview", "payload": historical_payload}, ports
    )
    committed = handle_general_command(
        {
            "action": "pricingImportCommit",
            "payload": preview["pricingPayload"],
            "conflictPolicy": "overwrite",
        },
        ports,
    )

    assert committed["pricingImportResult"]["addedCount"] == 1
    current = state["config"].provider_settings["custom"].model_prices["gpt-test"]
    assert current.input == 3
    assert current.output == 4


def test_generic_save_cannot_bypass_pricing_version_workflow() -> None:
    state = {"config": UserConfig.defaults()}
    ports = _ports(state)
    versioned, _ = UserConfig.defaults().apply_price_updates(
        {"gpt-test": {"input": 1, "output": 2}},
        effective_at="2026-08-01T00:00:00Z",
        created_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    payload = state["config"].to_dict()
    payload["pricing_versions"] = [
        version.to_dict() for version in versioned.pricing_versions
    ]
    before = state["config"].to_dict()

    generic = handle_general_command(
        {"action": "save", "settings": payload}, ports
    )
    pricing = handle_general_command(
        {
            "action": "savePricing",
            "settings": payload,
            "effectiveAt": "2026-08-02T00:00:00Z",
        },
        ports,
    )

    assert generic["kind"] == "error"
    assert pricing["kind"] == "error"
    assert state["config"].to_dict() == before


def test_export_price_file_uses_current_prices_or_builtin_template(tmp_path: Path) -> None:
    state = {"config": UserConfig.defaults()}
    opened: list[Path] = []
    ports = _ports(
        state,
        pricing_open_path=lambda path: opened.append(path),
    )

    with patch("codex_usage_hud.runtime_commands.hud_program_root", return_value=tmp_path):
        exported = handle_general_command({"action": "pricingExport"}, ports)
        exported_path = Path(str(exported["pricingPath"]))
        opened_status = handle_general_command(
            {"action": "pricingOpen", "filename": exported["filename"]}, ports
        )

    assert exported["pricingUsedTemplate"] is False
    assert exported["filename"] == "codex-usage-hud-pricing.json"
    assert exported_path == tmp_path / str(exported["filename"])
    assert json.loads(exported_path.read_text(encoding="utf-8"))["prices"]
    assert not opened_status["kind"]
    assert opened == [exported_path]

    state["config"] = replace(
        UserConfig.defaults(),
        model_prices={},
        pricing_versions=(),
        pricing_audit=(),
    )
    with patch("codex_usage_hud.runtime_commands.hud_program_root", return_value=tmp_path):
        fallback = handle_general_command({"action": "pricingExport"}, ports)

    fallback_path = Path(str(fallback["pricingPath"]))
    fallback_prices = json.loads(fallback_path.read_text(encoding="utf-8"))["prices"]
    assert fallback["pricingUsedTemplate"] is True
    assert fallback_path == exported_path
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert len(fallback_prices) == 1
    assert fallback_prices[0]["model"] == "gpt-5.6-sol"
    assert fallback_prices[0]["input"] == 5.0
    assert fallback_prices[0]["cached_input"] == 0.5
    assert fallback_prices[0]["cache_write"] == 6.25
    assert fallback_prices[0]["output"] == 30.0
    assert fallback_prices[0]["reasoning"] == 30.0


def test_export_and_recalculation_callbacks() -> None:
    state = {"config": UserConfig.defaults()}
    scopes: list[tuple[str, dict[str, object]]] = []
    ports = _ports(
        state,
        pricing_recalculation_preview=lambda scope: (
            scopes.append(("preview", dict(scope)))
            or {"matchedCount": 2, "changedCount": 1}
        ),
        pricing_recalculation_execute=lambda scope: (
            scopes.append(("execute", dict(scope)))
            or {"matchedCount": 2, "changedCount": 1}
        ),
    )

    preview = handle_general_command(
        {"action": "pricingRecalculationPreview", "provider": "custom"}, ports
    )
    executed = handle_general_command(
        {"action": "pricingRecalculationExecute", "provider": "custom"}, ports
    )

    assert preview["pricingRecalculationPreview"]["changedCount"] == 1
    assert executed["pricingRecalculationResult"]["changedCount"] == 1
    assert [kind for kind, _scope in scopes] == ["preview", "execute"]


def test_pricing_impact_preview_is_read_only_and_correlated() -> None:
    state = {"config": UserConfig.defaults()}
    received: list[dict[str, object]] = []

    def preview(command: dict[str, object]) -> dict[str, object]:
        received.append(dict(command))
        return {
            "effectiveAt": command["effectiveAt"],
            "before": {"recordCount": 2, "costUsd": 1.0},
            "after": {"recordCount": 3, "costUsd": 2.5},
        }

    ports = _ports(state, pricing_impact_preview=preview)
    before = state["config"].to_dict()
    result = dispatch_command(
        {
            "action": "pricingImpactPreview",
            "requestId": "impact-1",
            "settings": before,
            "effectiveAt": "2026-08-04T12:00:00Z",
        },
        RuntimeCommandPorts(),
        ports,
    )

    assert result["requestId"] == "impact-1"
    assert result["action"] == "pricingImpactPreview"
    assert result["pricingImpactPreview"]["after"]["costUsd"] == 2.5
    assert received[0]["effectiveAt"] == "2026-08-04T12:00:00Z"
    assert state["config"].to_dict() == before


def test_snapshot_impact_preview_prices_each_row_only_once(tmp_path: Path) -> None:
    ledger = PricingSnapshotLedger(tmp_path / "pricing.sqlite3")
    occurred_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    ledger.record_if_absent(
        event_key=ledger.event_key("session", 1, occurred_at),
        session_id="session",
        event_line=1,
        occurred_at=occurred_at,
        provider="custom",
        model="gpt-test",
        base_url="",
        input_tokens=1,
        cached_input_tokens=0,
        cache_write_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_usd=1.0,
        status="versioned",
        price_snapshot={"version_id": "old"},
    )
    calls = 0

    def resolver(_stored: object):
        nonlocal calls
        calls += 1
        return 2.0, "versioned", {"version_id": "new"}

    impact = _snapshot_pricing_impact(
        ledger,
        resolver,
        effective_at="2026-08-01T00:00:00Z",
    )

    assert calls == 1
    assert impact["after"]["previousCostUsd"] == 1.0
    assert impact["after"]["nextCostUsd"] == 2.0


def test_pricing_impact_preview_without_callback_is_explicitly_unavailable() -> None:
    state = {"config": UserConfig.defaults()}
    result = handle_general_command(
        {
            "action": "pricingImpactPreview",
            "effectiveAt": "2026-08-04T12:00:00Z",
            "settings": state["config"].to_dict(),
        },
        _ports(state),
    )
    assert result["kind"] == "error"
    assert "pricingImpactPreview" not in result
