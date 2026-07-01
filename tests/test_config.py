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
                            "completion": 7.5,
                            "reasoning": 8.0,
                        },
                    }
                ]
            }
        )

        self.assertEqual(prices["custom-model"].input, 1.25)
        self.assertEqual(prices["custom-model"].cached_input, 0.125)
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
