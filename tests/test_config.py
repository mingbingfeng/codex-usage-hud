"""Unit tests for user configuration handling."""

from __future__ import annotations

from datetime import datetime
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
    UserConfig,
    UserConfigStore,
    effective_display_mode,
    extract_model_prices,
    normalize_display_mode,
    write_json_object,
)
from codex_usage_hud.cli import current_budget_windows


class UserConfigStoreTests(unittest.TestCase):
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
        self.assertEqual(config.provider_settings["muyuan"].weekly_adjustment_usd, 2.5)
        self.assertEqual(config.provider_price_table("muyuan")["gpt-5"]["provider"], "muyuan")
        self.assertEqual(config.to_dict()["provider_settings"]["muyuan"]["pricing_url"], "https://pricing.example/muyuan.json")
        self.assertEqual(config.to_dict()["notification_only_providers"], ["notice"])

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
