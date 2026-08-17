"""Unit tests for user configuration handling."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.config import (
    MAX_PRICING_RESPONSE_BYTES,
    ModelPrice,
    PriceVersion,
    UserConfig,
    UserConfigStore,
    effective_display_mode,
    extract_model_prices,
    fetch_model_prices,
    normalize_display_mode,
    write_json_object,
)
from codex_usage_hud.cli import current_budget_windows
from codex_usage_hud.pricing import (
    PRICING_UNIT,
    PricingConflictError,
    empty_pricing_template,
    minimal_price_example,
)


class UserConfigStoreTests(unittest.TestCase):
    def test_failed_legacy_migration_write_keeps_file_time_version_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            path.write_text(
                json.dumps(
                    {"user": {"model_prices": {"custom": {"input": 1, "output": 2}}}}
                ),
                encoding="utf-8",
            )
            expected = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            store = UserConfigStore(path)

            with patch(
                "codex_usage_hud.config.write_json_object",
                side_effect=OSError("read-only"),
            ):
                first = store.load()
                second = store.load()

        self.assertEqual(first.pricing_versions, second.pricing_versions)
        self.assertEqual(first.pricing_versions[0].effective_at, expected)

    def test_store_persists_legacy_price_migration_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            path.write_text(
                json.dumps(
                    {
                        "user": {
                            "model_prices": {
                                "custom": {"input": 1, "output": 2}
                            }
                        },
                        "future": {"keep": True},
                    }
                ),
                encoding="utf-8",
            )
            store = UserConfigStore(path)

            first = store.load()
            second = store.load()
            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(first.pricing_versions, second.pricing_versions)
        self.assertEqual(len(first.pricing_versions), 1)
        self.assertEqual(
            persisted["user"]["pricing_versions"][0]["version_id"],
            first.pricing_versions[0].version_id,
        )
        self.assertEqual(persisted["future"], {"keep": True})

    def test_legacy_migration_preserves_existing_local_budget_and_scope(self) -> None:
        """New code defaults must not overwrite a persisted local HUD setup."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            path.write_text(
                json.dumps(
                    {
                        "user": {
                            "daily_budget_usd": 17.25,
                            "weekly_budget_usd": 125.5,
                            "provider_scope_mode": "custom",
                            "selected_providers": ["local-provider"],
                            "model_prices": {
                                "custom": {"input": 1, "output": 2}
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            store = UserConfigStore(path)
            changed_code_defaults = UserConfig(
                daily_budget_usd=999.0,
                weekly_budget_usd=1999.0,
            )

            with patch.object(
                UserConfig, "defaults", return_value=changed_code_defaults
            ):
                config = store.load()
            persisted = json.loads(path.read_text(encoding="utf-8"))["user"]

        self.assertEqual(config.daily_budget_usd, 17.25)
        self.assertEqual(config.weekly_budget_usd, 125.5)
        self.assertEqual(config.provider_scope_mode, "custom")
        self.assertEqual(config.selected_providers, ["local-provider"])
        self.assertEqual(persisted["daily_budget_usd"], 17.25)
        self.assertEqual(persisted["weekly_budget_usd"], 125.5)
        self.assertEqual(persisted["provider_scope_mode"], "custom")
        self.assertEqual(persisted["selected_providers"], ["local-provider"])

    def test_price_fetch_allows_only_bounded_http_responses(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTP"):
            fetch_model_prices("file:///tmp/prices.json")

        class OversizedResponse:
            def __enter__(self) -> "OversizedResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, limit: int) -> bytes:
                self.limit = limit
                return b"x" * limit

        response = OversizedResponse()
        with patch("codex_usage_hud.config.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "too large"):
                fetch_model_prices("https://pricing.example/prices.json")
        self.assertEqual(response.limit, MAX_PRICING_RESPONSE_BYTES + 1)

    def test_legacy_prices_migrate_to_stable_immutable_versions(self) -> None:
        migration_at = datetime(2026, 8, 5, 4, 30, tzinfo=timezone.utc)
        raw = {
            "model_prices": {
                "custom-model": {
                    "input": 1.5,
                    "cached_input": 0.15,
                    "cache_write": 1.75,
                    "output": 8.0,
                    "reasoning": 8.0,
                }
            }
        }

        first = UserConfig.from_dict(raw, migration_at=migration_at)
        second = UserConfig.from_dict(raw, migration_at=migration_at)

        self.assertIsInstance(first.pricing_versions, tuple)
        self.assertEqual(first.pricing_versions, second.pricing_versions)
        self.assertEqual(len(first.pricing_versions), 1)
        version = first.pricing_versions[0]
        self.assertEqual(version.created_by, "builtin_migration")
        self.assertEqual(version.source, "builtin")
        self.assertEqual(version.effective_at, migration_at)
        with self.assertRaises(FrozenInstanceError):
            version.input = 9  # type: ignore[misc]

        persisted = UserConfig.from_dict(first.to_dict())
        self.assertEqual(persisted.pricing_versions, first.pricing_versions)

    def test_price_version_rejects_negative_and_nonfinite_prices(self) -> None:
        base = {
            "model": "custom-model",
            "input": 1,
            "output": 2,
            "effective_at": "2026-08-01T00:00:00Z",
        }
        for field, invalid in (("input", -1), ("output", float("nan")), ("input", float("inf"))):
            with self.subTest(field=field, invalid=invalid):
                with self.assertRaises(ValueError):
                    PriceVersion.from_mapping({**base, field: invalid})

    def test_export_round_trip_preserves_every_version_field(self) -> None:
        now = datetime(2026, 8, 5, 5, 0, tzinfo=timezone.utc)
        initial = UserConfig.defaults()
        updated, _result = initial.apply_price_updates(
            {
                "custom-*": ModelPrice(
                    input=1.25,
                    cached_input=0.125,
                    cache_write=1.5,
                    output=7.5,
                    reasoning=8.0,
                    model="custom-*",
                    base_url="https://api.example.com/v1/",
                )
            },
            effective_at=now - timedelta(days=1),
            provider="Vendor-A",
            created_at=now,
        )

        exported = updated.export_pricing_payload()
        self.assertEqual(exported["schema_version"], 1)
        self.assertEqual(exported["unit"], PRICING_UNIT)
        preview = UserConfig.defaults().preview_pricing_import(exported, now=now)
        imported, result = UserConfig.defaults().apply_pricing_import(
            preview,
            conflict_policy="cancel",
            applied_at=now,
        )

        self.assertEqual(result.added_count, 1)
        self.assertEqual(imported.pricing_versions, updated.pricing_versions)
        row = imported.pricing_versions[0]
        self.assertEqual(row.provider, "vendor-a")
        self.assertEqual(row.base_url, "https://api.example.com/v1")
        self.assertEqual(row.match_pattern, "custom-*")
        self.assertEqual(float(row.cache_write), 1.5)

    def test_default_price_export_includes_visible_builtin_prices(self) -> None:
        exported = UserConfig.defaults().export_pricing_payload()

        self.assertEqual(exported["schema_version"], 1)
        self.assertEqual(exported["unit"], PRICING_UNIT)
        self.assertTrue(exported["prices"])
        self.assertTrue(
            any(
                row.get("model") == "gpt-5.6-sol"
                for row in exported["prices"]
                if isinstance(row, dict)
            )
        )

    def test_new_users_start_with_the_current_default_model_prices(self) -> None:
        prices = UserConfig.defaults().model_prices

        self.assertEqual(
            set(prices),
            {
                "gpt-5.4",
                "gpt-5.4-mini",
                "gpt-5.5",
                "gpt-5.6-luna",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
            },
        )
        self.assertEqual(
            (
                prices["gpt-5.6-terra"].input,
                prices["gpt-5.6-terra"].cached_input,
                prices["gpt-5.6-terra"].cache_write,
                prices["gpt-5.6-terra"].output,
                prices["gpt-5.6-terra"].reasoning,
            ),
            (2.0, 0.2, 2.5, 12.0, 12.0),
        )
        self.assertEqual(
            (
                prices["gpt-5.6-luna"].input,
                prices["gpt-5.6-luna"].cached_input,
                prices["gpt-5.6-luna"].cache_write,
                prices["gpt-5.6-luna"].output,
                prices["gpt-5.6-luna"].reasoning,
            ),
            (0.2, 0.02, 0.25, 1.2, 1.2),
        )

    def test_empty_template_and_minimal_example_are_unambiguous(self) -> None:
        template = empty_pricing_template()
        self.assertEqual(template["prices"], [])
        self.assertIn("effective_at", template["__description"])

        example_payload = {
            "schema_version": 1,
            "unit": PRICING_UNIT,
            "prices": [minimal_price_example()],
        }
        preview = UserConfig.defaults().preview_pricing_import(
            example_payload,
            now=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(preview.added_count, 1)

    def test_invalid_import_is_atomic_and_conflicts_require_resolution(self) -> None:
        now = datetime(2026, 8, 5, 5, 0, tzinfo=timezone.utc)
        original, _result = UserConfig.defaults().apply_price_updates(
            {"custom": {"input": 1, "output": 2}},
            effective_at=now - timedelta(days=1),
            provider="vendor-a",
            created_at=now,
        )
        original_version = original.pricing_versions[0]
        invalid = {
            "schema_version": 1,
            "unit": PRICING_UNIT,
            "prices": [
                {
                    "model": "custom",
                    "provider": "vendor-a",
                    "input": -1,
                    "output": 3,
                    "effective_at": "2026-08-04T05:00:00Z",
                }
            ],
        }
        with self.assertRaises(ValueError):
            original.preview_pricing_import(invalid, now=now)
        self.assertEqual(original.pricing_versions, (original_version,))

        replacement_payload = {
            "schema_version": 1,
            "unit": PRICING_UNIT,
            "prices": [
                {
                    "model": "custom",
                    "provider": "vendor-a",
                    "input": 4,
                    "output": 5,
                    "effective_at": "2026-08-04T05:00:00Z",
                }
            ],
        }
        preview = original.preview_pricing_import(replacement_payload, now=now)
        self.assertEqual(preview.updated_count, 1)
        self.assertEqual(len(preview.conflicts), 1)
        with self.assertRaises(PricingConflictError):
            original.apply_pricing_import(preview, conflict_policy="cancel", applied_at=now)
        self.assertEqual(original.pricing_versions, (original_version,))

        replaced, result = original.apply_pricing_import(
            preview,
            conflict_policy="overwrite",
            applied_at=now,
        )
        self.assertEqual(result.updated_count, 1)
        self.assertEqual(len(replaced.pricing_versions), 1)
        self.assertNotEqual(replaced.pricing_versions[0].version_id, original_version.version_id)
        self.assertEqual(result.audit[0].replaced_version_id, original_version.version_id)

    def test_user_edit_rejects_future_or_naive_effective_time_atomically(self) -> None:
        now = datetime(2026, 8, 5, 5, 0, tzinfo=timezone.utc)
        original = UserConfig.defaults()
        for effective_at in (now + timedelta(seconds=1), datetime(2026, 8, 1)):
            with self.subTest(effective_at=effective_at):
                with self.assertRaises(ValueError):
                    original.apply_price_updates(
                        {"custom": {"input": 1, "output": 2}},
                        effective_at=effective_at,
                        created_at=now,
                    )
        self.assertEqual(original.pricing_versions, ())

    def test_save_preserves_geometry_and_unknown_top_level_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            path.write_text(
                json.dumps(
                    {
                        "top": {"width": 420},
                        "request": {"height": 180},
                        "future": {"keep": True},
                    }
                ),
                encoding="utf-8",
            )
            store = UserConfigStore(path)

            config = UserConfig.defaults()
            config.weekly_adjustment_usd = 12.5
            store.save(config)

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["top"]["width"], 420)
            self.assertEqual(raw["request"]["height"], 180)
            self.assertEqual(raw["future"]["keep"], True)
            self.assertEqual(raw["user"]["weekly_adjustment_usd"], 12.5)

    def test_extract_model_prices_accepts_model_list_payload(self) -> None:
        prices = extract_model_prices(
            {
                "models": [
                    {
                        "id": "custom-model",
                        "pricing": {
                            "input": 1.25,
                            "cached": 0.125,
                            "cache_creation": 1.5625,
                            "completion": 7.5,
                            "reasoning": 8.0,
                        },
                    }
                ]
            }
        )

        self.assertEqual(prices["custom-model"].input, 1.25)
        self.assertEqual(prices["custom-model"].cached_input, 0.125)
        self.assertEqual(prices["custom-model"].cache_write, 1.5625)
        self.assertEqual(prices["custom-model"].output, 7.5)
        self.assertEqual(prices["custom-model"].reasoning, 8.0)

    def test_model_price_profiles_preserve_optional_provider_scope(self) -> None:
        config = UserConfig.from_dict(
            {
                "model_prices": {
                    "vendor-a/custom-model": {
                        "model": "custom-model",
                        "provider": "vendor-a",
                        "base_url": "https://api.vendor-a.example/v1/",
                        "input": 1.25,
                        "cached_input": 0.125,
                        "output": 7.5,
                        "reasoning": 8.0,
                    }
                }
            }
        )

        payload = config.to_dict()["model_prices"]["vendor-a/custom-model"]

        self.assertEqual(payload["model"], "custom-model")
        self.assertEqual(payload["provider"], "vendor-a")
        self.assertEqual(payload["base_url"], "https://api.vendor-a.example/v1")
        self.assertEqual(payload["input"], 1.25)

    def test_provider_settings_and_custom_scope_round_trip(self) -> None:
        config = UserConfig.from_dict(
            {
                "provider_scope_mode": "custom",
                "selected_providers": ["Muyuan", "custom", "muyuan"],
                "notification_only_providers": ["Notice", "muyuan", "notice"],
                "quick_launch_providers": ["Muyuan", "custom", "muyuan"],
                "provider_settings": {
                    "muyuan": {
                        "pricing_url": "https://pricing.example/muyuan.json",
                        "weekly_adjustment_usd": 2.5,
                        "model_prices": {
                            "gpt-5": {"input": 2, "cached_input": 1, "output": 4, "reasoning": 4}
                        },
                    }
                },
            }
        )

        self.assertEqual(config.provider_scope_mode, "custom")
        self.assertEqual(config.selected_providers, ["custom", "muyuan"])
        self.assertEqual(config.notification_only_providers, ["notice"])
        self.assertEqual(config.quick_launch_providers, ["custom", "muyuan"])
        self.assertEqual(config.provider_settings["muyuan"].weekly_adjustment_usd, 2.5)
        self.assertEqual(config.provider_price_table("muyuan")["gpt-5"]["provider"], "muyuan")
        self.assertEqual(config.to_dict()["provider_settings"]["muyuan"]["pricing_url"], "https://pricing.example/muyuan.json")
        self.assertEqual(config.to_dict()["notification_only_providers"], ["notice"])
        self.assertEqual(config.to_dict()["quick_launch_providers"], ["custom", "muyuan"])

    def test_provider_settings_collapse_legacy_prefixed_duplicate_prices(self) -> None:
        config = UserConfig.from_dict(
            {
                "provider_settings": {
                    "custom": {
                        "model_prices": {
                            "gpt-5.6-sol": {
                                "input": 2,
                                "cached_input": 0.2,
                                "output": 12,
                                "reasoning": 12,
                            },
                            "custom/gpt-5.6-sol": {
                                "model": "gpt-5.6-sol",
                                "provider": "custom",
                                "input": 5,
                                "cached_input": 0.5,
                                "output": 30,
                                "reasoning": 30,
                            },
                        }
                    }
                }
            }
        )

        prices = config.provider_settings["custom"].model_prices

        self.assertEqual(list(prices).count("gpt-5.6-sol"), 1)
        self.assertNotIn("custom/gpt-5.6-sol", prices)
        self.assertEqual(prices["gpt-5.6-sol"].input, 5.0)
        self.assertEqual(prices["gpt-5.6-sol"].provider, "custom")

    def test_provider_settings_collapse_duplicate_models_for_new_provider(self) -> None:
        config = UserConfig.from_dict(
            {
                "provider_settings": {
                    "cunai": {
                        "model_prices": {
                            "custom/gpt-5.6-sol": {
                                "model": "gpt-5.6-sol",
                                "provider": "custom",
                                "input": 5,
                                "cached_input": 0.5,
                                "output": 30,
                                "reasoning": 30,
                            },
                            "gpt-5.6-sol": {
                                "model": "gpt-5.6-sol",
                                "input": 1,
                                "cached_input": 0.1,
                                "output": 6,
                                "reasoning": 6,
                            },
                        }
                    }
                }
            }
        )

        prices = config.provider_settings["cunai"].model_prices

        self.assertEqual(list(prices).count("gpt-5.6-sol"), 1)
        self.assertNotIn("custom/gpt-5.6-sol", prices)
        self.assertEqual(prices["gpt-5.6-sol"].input, 1.0)

    def test_new_provider_gets_clean_defaults_and_notification_only_scope(self) -> None:
        config = UserConfig.from_dict(
            {
                "provider_scope_mode": "custom",
                "selected_providers": ["custom"],
                "provider_settings": {
                    "custom": {"weekly_adjustment_usd": 3.5},
                    "muyuan": {"weekly_adjustment_usd": 0.0},
                },
            }
        )

        migrated = config.migrate_legacy_provider_settings(
            ["custom", "muyuan", "cunai"], app_provider="custom"
        )

        self.assertEqual(list(migrated.provider_settings), ["custom", "muyuan", "cunai"])
        self.assertEqual(migrated.provider_order, ["custom", "muyuan", "cunai"])
        self.assertEqual(
            set(migrated.provider_settings["cunai"].model_prices),
            {"gpt-5.4", "gpt-5.4-mini", "gpt-5.5", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"},
        )
        self.assertEqual(
            migrated.provider_settings["cunai"].model_prices["gpt-5.6-terra"],
            UserConfig.defaults().model_prices["gpt-5.6-terra"],
        )
        self.assertEqual(
            migrated.provider_settings["cunai"].model_prices["gpt-5.6-luna"],
            UserConfig.defaults().model_prices["gpt-5.6-luna"],
        )
        self.assertEqual(migrated.provider_settings["cunai"].pricing_url, "")
        self.assertEqual(migrated.provider_settings["cunai"].weekly_adjustment_usd, 0.0)
        self.assertEqual(migrated.notification_only_providers, ["cunai"])

        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            store.save(migrated)
            self.assertEqual(store.load().provider_order, ["custom", "muyuan", "cunai"])

    def test_legacy_custom_scope_marks_unselected_provider_notification_only(self) -> None:
        config = UserConfig.from_dict(
            {
                "provider_scope_mode": "custom",
                "selected_providers": ["custom"],
                "model_prices": {
                    "gpt-5.6-sol": {
                        "input": 5,
                        "cached_input": 0.5,
                        "output": 30,
                        "reasoning": 30,
                    }
                },
            }
        )

        migrated = config.migrate_legacy_provider_settings(
            ["custom", "cunai"], app_provider="custom"
        )

        self.assertEqual(migrated.notification_only_providers, ["cunai"])

    def test_legacy_prices_migrate_per_discovered_provider_without_copying_adjustment(self) -> None:
        config = UserConfig.from_dict(
            {
                "pricing_url": "https://pricing.example/all.json",
                "weekly_adjustment_usd": 3.5,
                "model_prices": {
                    "shared": {"input": 1, "cached_input": 1, "output": 2, "reasoning": 2},
                    "muyuan-only": {"provider": "muyuan", "input": 5, "cached_input": 5, "output": 6, "reasoning": 6},
                },
            }
        ).migrate_legacy_provider_settings(["custom", "muyuan", "unknown"], app_provider="custom")

        self.assertEqual(set(config.provider_settings), {"custom", "muyuan"})
        self.assertIn("shared", config.provider_settings["custom"].model_prices)
        self.assertNotIn("muyuan-only", config.provider_settings["custom"].model_prices)
        self.assertEqual(config.provider_settings["muyuan"].model_prices["muyuan-only"].provider, "muyuan")
        self.assertEqual(config.provider_settings["custom"].weekly_adjustment_usd, 3.5)
        self.assertEqual(config.provider_settings["muyuan"].weekly_adjustment_usd, 0.0)

    def test_effective_provider_scopes_separate_statistics_from_notifications(self) -> None:
        config = UserConfig.from_dict(
            {
                "provider_scope_mode": "custom",
                "selected_providers": ["muyuan"],
                "notification_only_providers": ["notice", "muyuan"],
                "provider_settings": {
                    "custom": {"weekly_adjustment_usd": 3.0},
                    "muyuan": {"weekly_adjustment_usd": 2.0},
                    "unused": {"weekly_adjustment_usd": 7.0},
                },
            }
        )

        scope = config.effective_provider_scope("custom")
        notification_scope = config.effective_notification_provider_scope("custom")

        self.assertEqual(scope, frozenset({"custom", "muyuan"}))
        self.assertEqual(notification_scope, frozenset({"custom", "muyuan", "notice"}))
        self.assertEqual(config.weekly_adjustment_for_scope(scope), 5.0)
        config.provider_scope_mode = "all"
        self.assertIsNone(config.effective_provider_scope("custom"))
        self.assertIsNone(config.effective_notification_provider_scope("custom"))
        self.assertEqual(config.weekly_adjustment_for_scope(None), 12.0)

    def test_legacy_adjustment_remains_visible_before_provider_migration(self) -> None:
        config = UserConfig.from_dict({"weekly_adjustment_usd": 4.5})

        self.assertEqual(config.weekly_adjustment_for_scope(frozenset({"custom"})), 4.5)

    def test_user_config_normalizes_work_overlay_settings(self) -> None:
        config = UserConfig.from_dict(
            {
                "work_overlay_enabled": "off",
                "work_overlay_max_items": 99,
            }
        )

        self.assertEqual(config.work_overlay_max_items, 0)
        self.assertNotIn("work_overlay_enabled", config.to_dict())
        self.assertEqual(config.to_dict()["work_overlay_max_items"], 0)

    def test_user_config_normalizes_work_overlay_side(self) -> None:
        self.assertEqual(UserConfig.defaults().work_overlay_side, "right")
        self.assertEqual(
            UserConfig.from_dict({"work_overlay_side": "left"}).work_overlay_side,
            "left",
        )
        self.assertEqual(
            UserConfig.from_dict({"work_overlay_side": "invalid"}).work_overlay_side,
            "right",
        )
        self.assertEqual(
            UserConfig.from_dict({"work_overlay_side": "left"}).to_dict()[
                "work_overlay_side"
            ],
            "left",
        )

    def test_display_mode_normalizes_legacy_modes_to_renderer(self) -> None:
        self.assertEqual(normalize_display_mode("auto"), "renderer")
        self.assertEqual(normalize_display_mode("qt"), "renderer")
        self.assertEqual(normalize_display_mode("pyside6"), "renderer")
        self.assertEqual(normalize_display_mode("tk"), "renderer")
        self.assertEqual(normalize_display_mode("tkinter"), "renderer")
        self.assertEqual(normalize_display_mode("unknown"), "renderer")
        self.assertEqual(effective_display_mode("auto"), "renderer")
        self.assertEqual(effective_display_mode("renderer"), "renderer")
        self.assertEqual(effective_display_mode("qt"), "renderer")
        self.assertEqual(effective_display_mode("tk"), "renderer")

    def test_budget_windows_use_user_reset_day_and_time(self) -> None:
        config = UserConfig.defaults()
        config.daily_reset_time = "03:30"
        config.weekly_reset_weekday = 0
        config.weekly_reset_time = "04:00"

        day_start, week_start = current_budget_windows(
            config,
            now=datetime(2026, 6, 3, 2, 0).astimezone(),
        )

        self.assertEqual(day_start.hour, 3)
        self.assertEqual(day_start.minute, 30)
        self.assertEqual(day_start.day, 2)
        self.assertEqual(week_start.weekday(), 0)
        self.assertEqual(week_start.hour, 4)

    def test_write_json_object_retries_replace_after_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            original_replace = Path.replace
            call_count = {"count": 0}

            def flaky_replace(self: Path, target: Path) -> Path:
                call_count["count"] += 1
                if call_count["count"] == 1:
                    raise PermissionError("file is busy")
                return original_replace(self, target)

            with patch.object(Path, "replace", new=flaky_replace):
                write_json_object(path, {"ok": True})

            self.assertGreaterEqual(call_count["count"], 2)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})


if __name__ == "__main__":
    unittest.main()
